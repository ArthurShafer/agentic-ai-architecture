# Agentic Loop with Budget Awareness

The core pattern for autonomous AI tool use in production systems. This loop manages the conversation between an LLM and a set of tools, with multiple safety mechanisms to prevent runaway behavior and ensure quality output.

## The Problem

Naive agentic loops ("call tools until the model says stop") fail in production because:

1. **Models don't know their budget** — they'll keep calling tools indefinitely
2. **Diminishing returns** — after 3-4 rounds, tools often return redundant information
3. **Error cascading** — one failed tool can cause the model to retry fruitlessly
4. **Context bloat** — accumulated tool results can exceed the context window
5. **Short-circuiting** — models sometimes produce a thin response despite having rich data

## The Pattern

```
┌─────────────────────────────────────────┐
│            Agentic Loop                  │
│                                          │
│  for round in range(max_rounds):         │
│    ┌─────────────────────────────────┐   │
│    │  Stream LLM response            │   │
│    │  Collect text + tool_use blocks  │   │
│    └──────────┬──────────────────────┘   │
│               │                          │
│    ┌──────────▼──────────────────────┐   │
│    │  is_final? (end_turn / no tools)│   │
│    │  YES → break                    │   │
│    └──────────┬──────────────────────┘   │
│               │ NO                       │
│    ┌──────────▼──────────────────────┐   │
│    │  Execute each tool              │   │
│    │  - Timeout per tool             │   │
│    │  - Circuit break on 3 errors    │   │
│    │  - Emit SSE events              │   │
│    └──────────┬──────────────────────┘   │
│               │                          │
│    ┌──────────▼──────────────────────┐   │
│    │  Post-round processing:         │   │
│    │  1. Inject budget awareness     │   │
│    │  2. Inject data gap disclosure  │   │
│    │  3. Compact context if needed   │   │
│    │  4. Detect diminishing returns  │   │
│    └─────────────────────────────────┘   │
│                                          │
│  Max rounds hit → forced synthesis       │
│  Short response → rescue re-synthesis    │
└──────────────────────────────────────────┘
```

## Sub-Patterns

### Budget Awareness Injection

After each tool round, append a message telling the model its remaining capacity:

| Rounds Remaining | Message |
|-----------------|---------|
| > 3 | "You have {n} rounds and ~{tokens} tokens remaining." |
| 2-3 | "Begin converging toward your final answer." |
| 1 | "FINAL ROUND: You must synthesize all gathered information now." |

This is inspired by research on token-aware agent steering. Without it, models tend to either stop too early (wasting budget) or loop too long (wasting tokens).

### Diminishing Returns Detection

After round 2, extract key terms from each tool result and compare against a cumulative `seen_facts` set. If fewer than 5 new terms appear for 2 consecutive rounds, inject a synthesis nudge:

> "Recent tool calls returned mostly information you already have. Consider synthesizing your findings."

### Circuit Breaker

Track consecutive tool errors. At 3 consecutive failures:
- Inject: "Tool execution is experiencing issues. Do NOT call more tools. Synthesize from what you have."
- This prevents the model from burning rounds retrying a broken tool.

### Data Gap Disclosure

When a tool returns zero results, inject a mandatory disclosure:

> "[MANDATORY DATA GAP] The {tool_name} tool returned no results. You must not fabricate data for this area. Acknowledge the gap in your response."

This prevents hallucination when the model has no data to work with.

### Context Compaction

After round 3, estimate total context tokens. If exceeding 30K:
- Truncate old tool results (keep summary, trim raw data)
- Preserve the most recent 2 rounds in full
- This keeps the context window manageable without losing recent information

### Short-Response Rescue

After the loop completes, check if the streamed response is disproportionately short relative to the data gathered:

```
if tools_called >= 3 and visible_text_length < 1200:
    → Force a re-synthesis turn with explicit minimum length
```

This catches cases where the model "gives up" and produces a thin answer despite having rich tool data.

## Reference Implementation

See [`patterns/agentic_loop.py`](../patterns/agentic_loop.py) for a complete, runnable example.
