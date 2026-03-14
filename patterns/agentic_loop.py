"""
Agentic Loop with Budget Awareness, Circuit Breaking, and Diminishing Returns Detection.

This is a genericized reference implementation of the production pattern.
See docs/agentic-loop.md for full documentation.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

from .llm_provider import get_streaming_llm, StreamResult
from .tool_dispatch import execute_tool, ToolResult
from .sse_streaming import SSEEmitter, SSEEvent
from .trace_collector import TraceCollector


@dataclass(frozen=True)
class SurfaceConfig:
    """Configuration for a specific agent surface (e.g., full chat, sidebar, contextual)."""
    surface: str
    model_tier: str          # "fast" | "standard" | "advanced"
    max_tool_rounds: int     # Max agentic iterations
    max_tokens: int          # Per-response output limit
    tools: list[str] | None  # Tool whitelist (None = all)
    nudge_threshold: int | None = None  # Rounds before suggesting escalation


# Surface profiles — one engine, multiple depth levels
SURFACE_CONFIGS = {
    "full": SurfaceConfig(
        surface="full", model_tier="advanced",
        max_tool_rounds=20, max_tokens=8192, tools=None,
    ),
    "sidebar": SurfaceConfig(
        surface="sidebar", model_tier="standard",
        max_tool_rounds=3, max_tokens=2048,
        tools=["search", "lookup", "summarize", "calculate", "recent_activity", "help"],
        nudge_threshold=2,
    ),
    "contextual": SurfaceConfig(
        surface="contextual", model_tier="standard",
        max_tool_rounds=3, max_tokens=4096,
        tools=["search", "lookup", "summarize", "calculate", "get_details",
               "compare", "find_related", "get_timeline"],
        nudge_threshold=2,
    ),
}


@dataclass
class LoopState:
    """Mutable state tracked across agentic rounds."""
    seen_facts: set = field(default_factory=set)
    consecutive_errors: int = 0
    consecutive_low_novelty: int = 0
    total_tools_called: int = 0
    total_empty_results: int = 0
    streamed_text: str = ""


async def run_agentic_loop(
    messages: list[dict],
    config: SurfaceConfig,
    emitter: SSEEmitter,
    session_id: str,
) -> None:
    """
    Core agentic loop with budget awareness and safety mechanisms.

    Flow:
    1. Stream LLM response, collecting text + tool_use blocks
    2. If no tools requested (is_final), break
    3. Execute tools with timeout and circuit breaking
    4. Inject budget awareness, data gap disclosure
    5. Compact context if needed
    6. Detect diminishing returns
    7. If max rounds hit, force synthesis
    8. If response too short, force re-synthesis
    """
    provider = get_streaming_llm()
    trace = TraceCollector(session_id=session_id, surface=config.surface)
    state = LoopState()

    model = _select_model(messages, config)
    tools = _filter_tools(config.tools)

    for round_num in range(config.max_tool_rounds):
        round_start = time.time()

        # --- Stream LLM response ---
        iterator, finalizer = await provider.stream_with_tools(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=config.max_tokens,
        )

        async for chunk in iterator:
            await emitter.emit(SSEEvent("text", {"content": chunk}))
            state.streamed_text += chunk

        result: StreamResult = await finalizer()
        round_duration = time.time() - round_start

        trace.record_round(round_num, result.usage, result.tool_calls, round_duration)

        # --- Check if done ---
        if result.is_final or not result.tool_calls:
            break

        # --- Execute tools ---
        tool_results = []
        for tool_call in result.tool_calls:
            await emitter.emit(SSEEvent("tool_start", {
                "tool": tool_call.name,
                "description": f"Running {tool_call.name}...",
            }))

            try:
                tool_result = await asyncio.wait_for(
                    execute_tool(tool_call.name, tool_call.input),
                    timeout=30.0,
                )
                state.consecutive_errors = 0
                state.total_tools_called += 1

                if tool_result.count == 0:
                    state.total_empty_results += 1

                trace.record_tool(
                    name=tool_call.name,
                    duration_ms=(time.time() - round_start) * 1000,
                    result_count=tool_result.count,
                    error=None,
                    cached=tool_result.cached,
                )

            except Exception as e:
                state.consecutive_errors += 1
                tool_result = ToolResult(
                    summary=f"Error: {str(e)}", count=0, data=""
                )
                trace.record_tool(
                    name=tool_call.name,
                    duration_ms=(time.time() - round_start) * 1000,
                    result_count=0,
                    error=str(e),
                    cached=False,
                )

            await emitter.emit(SSEEvent("tool_result", {
                "tool": tool_call.name,
                "summary": tool_result.summary,
                "count": tool_result.count,
            }))

            tool_results.append((tool_call, tool_result))

        # --- Circuit breaker ---
        if state.consecutive_errors >= 3:
            messages.append({
                "role": "user",
                "content": (
                    "Tool execution is experiencing repeated failures. "
                    "Do NOT call more tools. Synthesize your answer from "
                    "the information you have already gathered."
                ),
            })
            break

        # --- Append tool results to conversation ---
        _append_tool_results(messages, result, tool_results)

        # --- Budget awareness injection ---
        remaining = config.max_tool_rounds - round_num - 1
        budget_msg = _build_budget_message(remaining, config.max_tokens, result.usage)
        if budget_msg:
            messages.append({"role": "user", "content": budget_msg})

        # --- Data gap disclosure ---
        empty_tools = [name for name, tr in tool_results if tr.count == 0]
        if empty_tools:
            gap_msg = _build_data_gap_message(empty_tools)
            messages.append({"role": "user", "content": gap_msg})

        # --- Context compaction ---
        if round_num >= 3:
            _compact_context(messages)

        # --- Diminishing returns detection ---
        if round_num >= 2:
            new_facts = _extract_key_terms(tool_results) - state.seen_facts
            state.seen_facts.update(new_facts)

            if len(new_facts) < 5:
                state.consecutive_low_novelty += 1
            else:
                state.consecutive_low_novelty = 0

            if state.consecutive_low_novelty >= 2:
                messages.append({
                    "role": "user",
                    "content": (
                        "Recent tool calls returned mostly information you already have. "
                        "Consider synthesizing your findings into a comprehensive answer."
                    ),
                })

        # --- Nudge to deeper surface ---
        if (config.nudge_threshold
                and round_num >= config.nudge_threshold
                and state.total_tools_called >= config.max_tool_rounds - 1):
            await emitter.emit(SSEEvent("nudge", {
                "message": "This question may benefit from a deeper analysis.",
                "target": "full",
            }))

    else:
        # Max rounds exhausted — force synthesis
        messages.append({
            "role": "user",
            "content": (
                "You have used all available tool rounds. Provide your final, "
                "comprehensive answer now using all information gathered. "
                "Do NOT call any more tools."
            ),
        })
        iterator, finalizer = await provider.stream_with_tools(
            messages=messages, tools=[], model=model, max_tokens=config.max_tokens,
        )
        async for chunk in iterator:
            await emitter.emit(SSEEvent("text", {"content": chunk}))
            state.streamed_text += chunk
        await finalizer()

    # --- Short-response rescue ---
    if state.total_tools_called >= 3 and len(state.streamed_text) < 1200:
        messages.append({
            "role": "user",
            "content": (
                "Your response was too brief given the data gathered. "
                "Please provide a more comprehensive synthesis of all tool results. "
                "Minimum 1500 characters."
            ),
        })
        state.streamed_text = ""
        iterator, finalizer = await provider.stream_with_tools(
            messages=messages, tools=[], model=model, max_tokens=config.max_tokens,
        )
        async for chunk in iterator:
            await emitter.emit(SSEEvent("text", {"content": chunk}))
        await finalizer()

    # --- Emit done + persist trace ---
    final_trace = trace.finalize()
    await emitter.emit(SSEEvent("done", {
        "tokens_used": final_trace.total_tokens,
        "tools_called": final_trace.total_tool_calls,
    }))

    # Fire-and-forget persistence
    asyncio.create_task(_save_trace(trace))


# --- Helper functions ---

def _build_budget_message(remaining: int, max_tokens: int, usage) -> str | None:
    """Build budget awareness message based on remaining rounds."""
    if remaining > 3:
        return f"You have {remaining} tool rounds remaining."
    elif remaining >= 2:
        return (
            f"You have {remaining} tool rounds remaining. "
            "Begin converging toward your final answer."
        )
    elif remaining == 1:
        return (
            "FINAL ROUND: You must synthesize all gathered information now. "
            "Do not start new lines of investigation."
        )
    return None


def _build_data_gap_message(empty_tools: list[str]) -> str:
    """Build mandatory data gap disclosure."""
    tools_str = ", ".join(empty_tools)
    return (
        f"[MANDATORY DATA GAP] The following tools returned no results: {tools_str}. "
        "You must not fabricate or assume data for these areas. "
        "Acknowledge the gap explicitly in your response."
    )


def _compact_context(messages: list[dict], max_estimated_tokens: int = 30000):
    """Truncate old tool results to manage context window size."""
    estimated = sum(len(str(m.get("content", ""))) // 4 for m in messages)
    if estimated <= max_estimated_tokens:
        return

    # Keep system prompt (index 0), last 4 messages, truncate middle
    for i in range(1, len(messages) - 4):
        content = messages[i].get("content", "")
        if isinstance(content, list):
            # Truncate tool result blocks
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    text = block.get("content", "")
                    if len(text) > 500:
                        block["content"] = text[:500] + "\n... [truncated for context management]"


def _extract_key_terms(tool_results: list[tuple]) -> set[str]:
    """Extract key terms from tool results for novelty detection."""
    terms = set()
    for _, result in tool_results:
        words = result.data.lower().split()
        # Simple extraction: unique words > 5 chars (not stop words)
        terms.update(w for w in words if len(w) > 5)
    return terms


def _select_model(messages: list[dict], config: SurfaceConfig) -> str:
    """Select model based on query complexity classification."""
    # In production, this uses a classifier. Simplified here.
    return config.model_tier


def _filter_tools(tool_whitelist: list[str] | None) -> list[dict]:
    """Filter tool registry to only include whitelisted tools."""
    from .tool_dispatch import TOOL_REGISTRY
    if tool_whitelist is None:
        return TOOL_REGISTRY
    return [t for t in TOOL_REGISTRY if t["name"] in tool_whitelist]


def _append_tool_results(messages, result, tool_results):
    """Append tool results to the conversation in the expected format."""
    # Append the assistant's tool-use message
    messages.append({
        "role": "assistant",
        "content": result.raw_content,
    })
    # Append tool results
    tool_result_blocks = []
    for tool_call, tool_result in tool_results:
        tool_result_blocks.append({
            "type": "tool_result",
            "tool_use_id": tool_call.id,
            "content": f"{tool_result.summary}\n\n{tool_result.data}",
        })
    messages.append({"role": "user", "content": tool_result_blocks})


async def _save_trace(trace: TraceCollector):
    """Fire-and-forget trace persistence."""
    try:
        await trace.save()
    except Exception as e:
        import logging
        logging.warning(f"Trace save failed (non-fatal): {e}")
