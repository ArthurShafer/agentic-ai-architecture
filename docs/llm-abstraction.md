# LLM Provider Abstraction

Strategy pattern with singleton registry for provider-agnostic AI agent systems. Swap LLM backends without touching agent logic.

## The Problem

Hardcoding `import anthropic` (or any provider SDK) throughout an agent system creates:
- **Vendor lock-in** — switching providers requires touching every file
- **Testing friction** — can't mock the LLM layer cleanly
- **Schema fragmentation** — different providers use different tool schema formats
- **Pricing complexity** — prompt caching, batch pricing, etc. vary by provider

## Architecture

```
┌─────────────────────────────────────────┐
│         Agent / Service Layer            │
│  (Calls get_streaming_llm() only)        │
└──────────────────┬──────────────────────┘
                   │
┌──────────────────▼──────────────────────┐
│            Registry (Singleton)          │
│  get_streaming_llm() → cached provider   │
│  Provider selected via LLM_PROVIDER env  │
└──────────────────┬──────────────────────┘
                   │
┌──────────────────▼──────────────────────┐
│      AsyncStreamingProvider (ABC)         │
│  ┌─────────────────────────────────────┐ │
│  │ stream_with_tools() → (iter, final) │ │
│  │ normalize_tool_result()             │ │
│  │ normalize_assistant_content()       │ │
│  │ format_tools()                      │ │
│  │ is_available()                      │ │
│  └─────────────────────────────────────┘ │
└──────────────────┬──────────────────────┘
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
   Anthropic    OpenAI    Custom
   Provider    Provider   Provider
```

## Key Patterns

### The StreamFinalizer Pattern

`stream_with_tools()` returns a tuple: `(AsyncIterator[str], StreamFinalizer)`

```python
async def stream_with_tools(self, messages, tools, model, ...):
    # Returns two things:
    # 1. An async iterator that yields text chunks in real-time
    # 2. A finalizer callable that returns the complete result after iteration
    return text_iterator, finalizer

# Usage:
iterator, finalizer = await provider.stream_with_tools(...)

async for chunk in iterator:
    yield SSEEvent("text", {"content": chunk})  # Stream to user

result: StreamResult = await finalizer()  # Collect metadata
# result.tool_calls, result.usage, result.is_final
```

**Why separate?** Streaming text to the user and collecting structured metadata (tool calls, token usage, stop reason) are two different concerns with different timing requirements. The iterator handles real-time delivery; the finalizer handles post-stream aggregation.

### Provider-Agnostic Data Types

```python
@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict

@dataclass
class StreamResult:
    text: str
    tool_calls: list[ToolUseBlock]
    usage: TokenUsage
    is_final: bool  # Normalizes vendor-specific stop reasons
```

`is_final` is the key normalization — Anthropic uses `"end_turn"`, OpenAI uses `"stop"`, others vary. One boolean check replaces vendor-specific string comparisons throughout the agent loop.

### Tier-Based Model Selection

Config uses abstract tier names, not model IDs:

```python
SURFACE_CONFIGS = {
    "war_room": SurfaceConfig(model_tier="advanced", ...),
    "sidebar": SurfaceConfig(model_tier="fast", ...),
}

def get_model_for_tier(tier: str) -> str:
    return {
        "fast": "claude-haiku-4-5-20251001",
        "standard": "claude-sonnet-4-20250514",
        "advanced": "claude-opus-4-20250514",
    }[tier]
```

When Anthropic releases new models, you update one mapping function — not every surface config.

### Canonical Schema Format

Tool schemas use Anthropic's format (`input_schema`) as the canonical representation. The `format_tools()` method on each provider translates to its native format:

```python
# Canonical (stored in registry):
{"name": "search", "description": "...", "input_schema": {"type": "object", ...}}

# Anthropic provider: pass through as-is
# OpenAI provider: transform to {"name": "search", "parameters": {...}}
```

This avoids maintaining N copies of tool schemas in N formats.
