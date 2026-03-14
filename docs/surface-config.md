# Multi-Surface Agent Configuration

A pattern for serving multiple UI surfaces with different depth, cost, and capability profiles from a single agent engine.

## The Problem

A production AI platform typically has multiple interaction points:
- A **full-depth executive briefing room** (expensive, thorough)
- A **sidebar assistant** (quick answers, cheap)
- A **contextual chat** panel (moderate depth, pre-loaded context)

Building separate agent systems for each creates massive code duplication. But using the same configuration everywhere either overspends on simple queries or underpowers complex ones.

## The Pattern: Frozen Configuration Objects

```python
@dataclass(frozen=True)
class SurfaceConfig:
    surface: str              # "war_room" | "sidebar" | "opp_chat"
    model_tier: str           # "fast" | "standard" | "advanced"
    max_tool_rounds: int      # Agentic loop depth cap
    max_tokens: int           # Per-response output token limit
    tools: list[str] | None   # Tool whitelist (None = all tools)
    pre_load_context: bool    # Inject domain data into system prompt
    nudge_threshold: int | None  # Rounds before suggesting escalation
```

### Surface Profiles

| Surface | Model | Max Rounds | Tools | Pre-load Context | Nudge |
|---------|-------|-----------|-------|-------------------|-------|
| **War Room** | advanced | 20 | All 27 | No | None |
| **Sidebar** | standard | 3 | 6 selected | No | Round 2 |
| **Opp Chat** | standard | 3 | 8 selected | Yes (opportunity data) | Round 2 |

### Graceful Degradation with Escalation Nudges

When a lighter surface detects a query exceeding its capability:

```python
if round >= config.nudge_threshold and tools_called >= config.max_tool_rounds - 1:
    yield NudgeEvent(
        message="This question may benefit from a deeper analysis.",
        target="war_room",
        query=original_query
    )
```

This creates a natural escalation path: Sidebar → Opp Chat → War Room, rather than failing silently or producing low-quality answers.

### Tier-Aware Query Classification

Before processing, classify query complexity (tiers 1-5):

| Tier | Example | Routed To |
|------|---------|-----------|
| 1 (Simple fact) | "What's the deadline for this RFP?" | standard model |
| 2 (Lookup) | "Show me recent awards for this agency" | standard model |
| 3 (Analysis) | "How should we position against competitors?" | advanced model |
| 4 (Multi-domain) | "Build me a win strategy with staffing plan" | advanced model |
| 5 (Conversational) | "Thanks!" / "What do you mean?" | standard model |

This saves ~60% on model costs by routing simple queries to cheaper models, even on the full-depth War Room surface.

## Why Frozen Dataclasses?

Using `frozen=True` ensures configs are immutable after creation. This prevents bugs where a surface's config gets accidentally mutated during request processing, and makes configs safe to share across concurrent requests.
