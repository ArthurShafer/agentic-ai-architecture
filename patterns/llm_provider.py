"""
LLM Provider Abstraction Layer.

Strategy pattern with singleton registry for provider-agnostic AI agent systems.
See docs/llm-abstraction.md for full documentation.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Awaitable, Any


# --- Provider-Agnostic Data Types ---

@dataclass
class TokenUsage:
    """Token usage tracking with prompt caching awareness."""
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ToolUseBlock:
    """A tool call requested by the LLM."""
    id: str
    name: str
    input: dict


@dataclass
class StreamResult:
    """Complete result after streaming finishes."""
    text: str
    tool_calls: list[ToolUseBlock]
    usage: TokenUsage
    is_final: bool  # Normalizes vendor-specific stop reasons
    raw_content: Any = None  # Provider-specific content for message assembly


# The finalizer callable type: called after streaming to get the complete result
StreamFinalizer = Callable[[], Awaitable[StreamResult]]


# --- Abstract Base Class ---

class AsyncStreamingProvider(ABC):
    """
    Abstract base class for LLM providers.

    Implementations must handle:
    - Streaming text output
    - Tool use protocol (tool calls + results)
    - Token usage tracking
    - Provider-specific schema translation
    """

    @abstractmethod
    async def stream_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> tuple[AsyncIterator[str], StreamFinalizer]:
        """
        Stream a response with tool use support.

        Returns:
            A tuple of (text_iterator, finalizer):
            - text_iterator: yields text chunks in real-time
            - finalizer: called after iteration to get complete result
                         (tool calls, usage, stop reason)

        Why two separate objects?
            Streaming text to the user and collecting structured metadata
            are different concerns with different timing. The iterator
            handles real-time delivery; the finalizer handles post-stream
            aggregation.
        """
        ...

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        model: str = "fast",
        max_tokens: int = 1024,
    ) -> str:
        """Simple non-streaming completion (used for grading, summarization)."""
        ...

    @abstractmethod
    def format_tools(self, tools: list[dict]) -> list[dict]:
        """
        Translate tool schemas from canonical format to provider-specific format.

        Canonical format uses Anthropic's schema (input_schema key).
        Providers that use different formats (e.g., OpenAI uses 'parameters')
        override this method.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is configured and reachable."""
        ...


# --- Anthropic Implementation ---

class AnthropicStreamingProvider(AsyncStreamingProvider):
    """
    Anthropic Claude streaming provider.

    Features:
    - Native tool use support
    - Prompt caching with cache_control markers
    - Extended thinking (if supported by model)
    """

    def __init__(self):
        import anthropic
        self._client = anthropic.AsyncAnthropic()

    async def stream_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> tuple[AsyncIterator[str], StreamFinalizer]:

        model_id = get_model_for_tier(model)

        kwargs = {
            "model": model_id,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = self.format_tools(tools)
        if system:
            # Wrap system prompt with cache_control for prompt caching
            kwargs["system"] = [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }]

        stream = await self._client.messages.create(**kwargs, stream=True)

        # Shared state between iterator and finalizer
        collected_text = []
        collected_tool_calls = []
        usage = None
        stop_reason = None
        raw_content = []

        async def text_iterator() -> AsyncIterator[str]:
            nonlocal usage, stop_reason, raw_content
            current_tool: dict | None = None

            async for event in stream:
                if event.type == "content_block_start":
                    if event.content_block.type == "text":
                        pass  # Text block started
                    elif event.content_block.type == "tool_use":
                        current_tool = {
                            "id": event.content_block.id,
                            "name": event.content_block.name,
                            "input_json": "",
                        }

                elif event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        chunk = event.delta.text
                        collected_text.append(chunk)
                        yield chunk  # Stream to user
                    elif hasattr(event.delta, "partial_json"):
                        if current_tool:
                            current_tool["input_json"] += event.delta.partial_json

                elif event.type == "content_block_stop":
                    if current_tool:
                        import json
                        collected_tool_calls.append(ToolUseBlock(
                            id=current_tool["id"],
                            name=current_tool["name"],
                            input=json.loads(current_tool["input_json"] or "{}"),
                        ))
                        current_tool = None

                elif event.type == "message_delta":
                    stop_reason = event.delta.stop_reason
                    if hasattr(event, "usage"):
                        usage = TokenUsage(
                            input_tokens=getattr(event.usage, "input_tokens", 0),
                            output_tokens=getattr(event.usage, "output_tokens", 0),
                            cache_read_tokens=getattr(event.usage, "cache_read_input_tokens", 0),
                            cache_creation_tokens=getattr(event.usage, "cache_creation_input_tokens", 0),
                        )

                elif event.type == "message_start":
                    if hasattr(event.message, "usage"):
                        u = event.message.usage
                        usage = TokenUsage(
                            input_tokens=getattr(u, "input_tokens", 0),
                            output_tokens=0,
                            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0),
                            cache_creation_tokens=getattr(u, "cache_creation_input_tokens", 0),
                        )

        async def finalizer() -> StreamResult:
            return StreamResult(
                text="".join(collected_text),
                tool_calls=collected_tool_calls,
                usage=usage or TokenUsage(0, 0),
                is_final=(stop_reason == "end_turn" or not collected_tool_calls),
                raw_content=raw_content,
            )

        return text_iterator(), finalizer

    async def complete(self, prompt: str, model: str = "fast", max_tokens: int = 1024) -> str:
        model_id = get_model_for_tier(model)
        response = await self._client.messages.create(
            model=model_id,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return response.content[0].text

    def format_tools(self, tools: list[dict]) -> list[dict]:
        """Anthropic uses the canonical format — pass through."""
        return tools

    def is_available(self) -> bool:
        import os
        return bool(os.environ.get("ANTHROPIC_API_KEY"))


# --- Registry (Singleton) ---

_PROVIDER_INSTANCE: AsyncStreamingProvider | None = None


def get_streaming_llm() -> AsyncStreamingProvider:
    """Get or create the singleton LLM provider."""
    global _PROVIDER_INSTANCE
    if _PROVIDER_INSTANCE is None:
        provider_name = _get_active_provider()
        if provider_name == "anthropic":
            _PROVIDER_INSTANCE = AnthropicStreamingProvider()
        else:
            raise ValueError(f"Unknown LLM provider: {provider_name}")
    return _PROVIDER_INSTANCE


def _get_active_provider() -> str:
    """Determine active provider from environment."""
    import os
    return os.environ.get("LLM_PROVIDER", "anthropic")


# --- Tier-Based Model Selection ---

MODEL_TIERS = {
    "fast": "claude-haiku-4-5-20251001",
    "standard": "claude-sonnet-4-20250514",
    "advanced": "claude-opus-4-20250514",
}


def get_model_for_tier(tier: str) -> str:
    """Resolve abstract tier name to concrete model ID."""
    if tier in MODEL_TIERS:
        return MODEL_TIERS[tier]
    # If a full model ID was passed, return as-is
    return tier
