"""
Tool Registry & Dispatch with Fresh Session Isolation, CRAG, and Output Sanitization.

This is a genericized reference implementation of the production pattern.
See docs/tool-dispatch.md for full documentation.
"""

import inspect
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Awaitable


@dataclass
class ToolResult:
    """Standardized return type for all tool handlers."""
    summary: str
    count: int
    data: str
    cached: bool = False


# --- Layer 1: Registry (Pure Data) ---
# Tool schemas in Anthropic's tool-use format.
# No imports, no logic, no handlers.

TOOL_REGISTRY = [
    {
        "name": "search_records",
        "description": (
            "Search records by keyword, category, or identifier. "
            "USE WHEN: user asks about specific items, records, or entries. "
            "DO NOT USE FOR: analytics, competitor analysis, or trend questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "category": {"type": "string", "description": "Optional category filter"},
                "limit": {"type": "integer", "description": "Max results", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_analytics",
        "description": (
            "Retrieve analytics and statistics for a given domain. "
            "USE WHEN: user asks about trends, counts, distributions, or metrics. "
            "DO NOT USE FOR: looking up specific records or items."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Analytics domain"},
                "time_range": {"type": "string", "description": "Time range (e.g., '30d', '1y')"},
            },
            "required": ["domain"],
        },
    },
    {
        "name": "get_deep_profile",
        "description": (
            "COMPOSITE TOOL: Get a comprehensive profile by combining record lookup, "
            "analytics, timeline, and related entities into a single call. "
            "USE WHEN: user asks for a deep dive, full analysis, or comprehensive overview. "
            "DO NOT USE FOR: simple lookups — use search_records instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Entity identifier"},
            },
            "required": ["entity_id"],
        },
    },
    # ... additional tools follow the same pattern
]


# --- Layer 2: Dispatch (Lazy Routing) ---

_DISPATCH_TABLE: dict[str, Callable[..., Awaitable[ToolResult]]] | None = None


def _build_dispatch_table() -> dict[str, Callable[..., Awaitable[ToolResult]]]:
    """
    Build the dispatch table with deferred imports.

    All handler imports happen inside this function body, preventing
    circular imports at module load time. Python caches imports after
    first invocation, so subsequent calls are essentially free.
    """
    # Import handlers lazily to avoid circular dependencies
    from .handlers import knowledge, analytics, composite

    return {
        "search_records": knowledge.search_records,
        "get_analytics": analytics.get_analytics,
        "get_deep_profile": composite.get_deep_profile,
        # ... map all tool names to their handlers
    }


def _get_dispatch_table() -> dict[str, Callable[..., Awaitable[ToolResult]]]:
    """Get or build the dispatch table (singleton pattern)."""
    global _DISPATCH_TABLE
    if _DISPATCH_TABLE is None:
        _DISPATCH_TABLE = _build_dispatch_table()
    return _DISPATCH_TABLE


# --- Layer 3: Execution Pipeline ---

_SEMANTIC_CACHE: dict[str, ToolResult] = {}  # In production, use Redis


async def execute_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    context: Any = None,
) -> ToolResult:
    """
    Execute a tool through the full pipeline:
    1. Cache check
    2. Fresh DB session isolation
    3. Dynamic signature inspection
    4. Execute handler
    5. Output sanitization
    6. CRAG (for retrieval tools)
    """
    # --- Stage 1: Cache check ---
    cache_key = f"{tool_name}:{_stable_hash(tool_input)}"
    if cache_key in _SEMANTIC_CACHE:
        result = _SEMANTIC_CACHE[cache_key]
        result.cached = True
        return result

    # --- Stage 2: Fresh DB session ---
    dispatch = _get_dispatch_table()
    handler = dispatch.get(tool_name)
    if not handler:
        return ToolResult(summary=f"Unknown tool: {tool_name}", count=0, data="")

    db = _get_fresh_session()  # Each tool gets its own session
    try:
        # --- Stage 3: Dynamic signature inspection ---
        sig = inspect.signature(handler)
        kwargs: dict[str, Any] = {"db": db}

        # Only pass parameters the handler actually accepts
        for param_name in sig.parameters:
            if param_name == "db":
                continue
            if param_name in tool_input:
                kwargs[param_name] = tool_input[param_name]
            elif context and hasattr(context, param_name):
                kwargs[param_name] = getattr(context, param_name)

        # --- Stage 4: Execute ---
        result = await handler(**kwargs)

        # --- Stage 5: Output sanitization ---
        result.summary = sanitize_for_llm(result.summary)
        result.data = sanitize_for_llm(redact_pii(result.data))

        # --- Stage 6: CRAG (for retrieval tools) ---
        if _is_retrieval_tool(tool_name) and result.count > 0:
            result = await _apply_crag(tool_name, tool_input, result)

        # Cache the result
        _SEMANTIC_CACHE[cache_key] = result
        return result

    except Exception as e:
        db.rollback()
        raise
    finally:
        db.close()  # Always close — prevents session poisoning


# --- CRAG (Corrective RAG) ---

async def _apply_crag(
    tool_name: str,
    original_input: dict,
    result: ToolResult,
    max_retries: int = 2,
) -> ToolResult:
    """
    Grade retrieval relevance and rewrite query if insufficient.

    Uses a fast model (Haiku-tier) to grade relevance:
    - RELEVANT → return as-is
    - INSUFFICIENT → rewrite query, re-retrieve
    - OFF_TOPIC → return empty with gap disclosure
    """
    from .llm_provider import get_streaming_llm

    provider = get_streaming_llm()

    for attempt in range(max_retries):
        grade_prompt = (
            f"Query: {original_input.get('query', '')}\n"
            f"Results summary: {result.summary}\n"
            f"First 500 chars of data: {result.data[:500]}\n\n"
            "Grade the relevance: RELEVANT, INSUFFICIENT, or OFF_TOPIC. "
            "Respond with exactly one word."
        )

        try:
            grade = await provider.complete(grade_prompt, model="fast", max_tokens=10)
            grade = grade.strip().upper()
        except Exception:
            return result  # On grading failure, return ungraded results

        if grade == "RELEVANT":
            return result
        elif grade == "OFF_TOPIC":
            return ToolResult(summary="No relevant results found", count=0, data="")
        elif grade == "INSUFFICIENT" and attempt < max_retries - 1:
            # Rewrite query
            rewrite_prompt = (
                f"Original query: {original_input.get('query', '')}\n"
                f"The results were insufficient. Rewrite the query to be more specific. "
                "Respond with only the new query."
            )
            new_query = await provider.complete(rewrite_prompt, model="fast", max_tokens=100)
            original_input["query"] = new_query.strip()

            # Re-execute the tool with rewritten query
            result = await execute_tool(tool_name, original_input)

    return result


# --- Sanitization ---

# Patterns that could be prompt injection attempts
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"you\s+are\s+now\s+a",
    r"system\s*:\s*",
    r"<\s*/?system\s*>",
    r"IMPORTANT\s*:\s*override",
]

_PII_PATTERNS = {
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "phone": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
}


def sanitize_for_llm(text: str) -> str:
    """Strip prompt injection patterns from tool output."""
    for pattern in _INJECTION_PATTERNS:
        text = re.sub(pattern, "[FILTERED]", text, flags=re.IGNORECASE)
    return text


def redact_pii(text: str) -> str:
    """Mask PII patterns in tool output."""
    for pii_type, pattern in _PII_PATTERNS.items():
        text = re.sub(pattern, f"[REDACTED_{pii_type.upper()}]", text)
    return text


# --- Utilities ---

def _is_retrieval_tool(tool_name: str) -> bool:
    """Check if a tool is a retrieval tool (eligible for CRAG)."""
    return tool_name in {"search_records", "search_documents", "query_knowledge_base"}


def _stable_hash(d: dict) -> str:
    """Create a stable hash for cache keys."""
    import hashlib
    import json
    return hashlib.md5(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]


def _get_fresh_session():
    """Get a fresh database session. In production, uses SQLAlchemy SessionLocal."""
    # Placeholder — in production this creates a new SQLAlchemy session
    class MockSession:
        def rollback(self): pass
        def close(self): pass
        def commit(self): pass
    return MockSession()
