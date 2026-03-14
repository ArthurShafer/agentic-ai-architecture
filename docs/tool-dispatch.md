# Tool Registry & Dispatch

A production pattern for managing AI agent tools at scale — separating schema definitions from execution logic, with per-tool database isolation, output sanitization, and corrective retrieval.

## The Problem

As an agent system grows beyond 5-10 tools, several issues emerge:

1. **Circular imports** — tool handlers need DB models, services need tool handlers
2. **Session poisoning** — one tool's failed DB query corrupts the session for subsequent tools
3. **Tool mis-selection** — LLMs pick the wrong tool ~15-20% of the time without guidance
4. **Prompt injection via results** — tool outputs may contain adversarial content
5. **Retrieval quality** — RAG tools sometimes return irrelevant results

## Architecture: Three-Layer Separation

```
┌──────────────────────────────────────────────────┐
│  Layer 1: REGISTRY (Pure Data)                    │
│  - Tool schemas in LLM provider format            │
│  - No imports, no logic, no handlers              │
│  - USE WHEN / DO NOT USE annotations              │
│  - Filterable by surface (war_room, sidebar, etc) │
└──────────────────────┬───────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────┐
│  Layer 2: DISPATCH (Lazy Routing)                 │
│  - Maps tool_name → async handler function        │
│  - Built on first call (deferred imports)         │
│  - Dynamic signature inspection per handler       │
│  - Cache check before execution                   │
└──────────────────────┬───────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────┐
│  Layer 3: HANDLERS (Domain Logic)                 │
│  - Grouped by domain (knowledge, competitors...)  │
│  - Each returns ToolResult {summary, count, data} │
│  - Own DB session per invocation                  │
│  - Pure business logic, no dispatch awareness     │
└──────────────────────────────────────────────────┘
```

## Key Patterns

### USE WHEN / DO NOT USE Annotations

Adding explicit dispatch guidance in tool descriptions reduced tool mis-selection by ~40%:

```python
{
    "name": "search_opportunities",
    "description": (
        "Search for federal contracting opportunities by keyword, agency, or NAICS code. "
        "USE WHEN: user asks about specific contracts, RFPs, solicitations, or opportunities. "
        "DO NOT USE FOR: competitor analysis, employee queries, or general market questions."
    ),
    "input_schema": { ... }
}
```

### Fresh DB Session Per Tool Call

Each tool execution creates its own database session:

```python
async def execute_tool(tool_name, tool_input, ...):
    tool_db = get_session()  # Fresh session
    try:
        result = await handler(db=tool_db, **kwargs)
        return result
    except Exception:
        tool_db.rollback()
        raise
    finally:
        tool_db.close()  # Always closed
```

This prevents "session poisoning" — if tool A runs a query that fails, the rolled-back session state doesn't corrupt tool B's queries in the same agentic round.

### Dynamic Signature Inspection

Rather than every handler accepting every possible parameter:

```python
sig = inspect.signature(handler)
kwargs = {}
if "notice_id" in sig.parameters:
    kwargs["notice_id"] = context.notice_id
if "user_id" in sig.parameters:
    kwargs["user_id"] = context.user_id
# Only pass what the handler actually accepts
result = await handler(db=tool_db, **kwargs)
```

### CRAG (Corrective RAG)

For retrieval tools, results are graded before being returned to the LLM:

```
1. Execute retrieval tool → get results
2. Grade relevance with fast model (Haiku)
   - "RELEVANT" → return results
   - "INSUFFICIENT" → rewrite query → re-retrieve (max 2 retries)
   - "OFF_TOPIC" → return empty with gap disclosure
3. Never blocks — timeout after 5 seconds falls through to raw results
```

### Output Sanitization Pipeline

All tool results pass through two filters before being injected into the LLM context:

1. **`sanitize_for_llm()`** — strips prompt injection patterns (e.g., "ignore previous instructions")
2. **`redact_pii()`** — masks SSNs, email addresses, phone numbers

### Composite Tools

For common multi-tool queries, "super-tools" combine 4-5 individual calls:

```python
# Instead of the model calling 5 tools sequentially:
#   search_opportunities → get_scoring → get_competitors → get_staffing → get_attachments

# One composite tool does it all:
async def get_opportunity_intelligence(notice_id: str, db):
    opp = await get_opportunity(notice_id, db)
    scores = await get_scoring(notice_id, db)
    competitors = await find_competitors(notice_id, db)
    staffing = await match_employees(notice_id, db)
    return combine_results(opp, scores, competitors, staffing)
```

This saves 3-4 agentic rounds (~$0.50-1.00 per query in token costs).

## Reference Implementation

See [`patterns/tool_dispatch.py`](../patterns/tool_dispatch.py) for a complete, runnable example.
