# Design Decisions — Lessons from Production

Every pattern in this repo exists because something broke, cost too much, or confused users in production. This document captures the "why" behind each decision.

---

## 1. Why Fresh DB Sessions Per Tool Call

**The failure:** During an agentic loop, Tool A ran a query that threw a database error. SQLAlchemy rolled back the session. Tool B then tried to query using the same session — but the ORM's identity map was corrupted. Tool B returned stale/wrong data. The LLM synthesized a confident, incorrect answer.

**The fix:** Every tool call creates its own database session, used only for that tool, and closed in a `finally` block regardless of outcome.

**The cost:** ~2ms overhead per tool call for session creation. Negligible compared to the LLM round-trip.

**The lesson:** In agentic systems, tools are not independent functions — they share conversational state through the LLM. But they must NOT share infrastructure state. A tool failure should be invisible to other tools.

---

## 2. Why Budget Awareness Injection

**The failure:** The agent would call 15+ tools on a simple question, burning $2-3 in tokens before producing a mediocre answer. On complex questions, it would stop after 2 tools because the first results looked "good enough."

**The paper:** Inspired by research on token-aware agent steering (arXiv:2511.17006). The key insight: LLMs have no inherent sense of budget. They don't know how many rounds they have left or how many tokens they've consumed.

**The fix:** After every tool round, inject a text message: "You have N rounds and ~X tokens remaining." In the final rounds, escalate to "FINAL ROUND — synthesize now."

**The result:** Tool usage dropped ~40% on simple queries. Complex queries now use their full budget instead of stopping early. Cost per query became predictable.

**The lesson:** Don't rely on the model to self-regulate. Give it explicit, quantitative constraints in natural language.

---

## 3. Why USE WHEN / DO NOT USE Tool Annotations

**The failure:** With 27 tools, the model would frequently pick the wrong tool. "Tell me about Lockheed Martin" would trigger `search_opportunities` instead of `get_competitor_profile`. The model would get back irrelevant results, try another wrong tool, waste 3 rounds, then apologize.

**The fix:** Added explicit dispatch guidance in tool descriptions:

```
"Search for federal contracting opportunities by keyword.
 USE WHEN: user asks about specific contracts, RFPs, or solicitations.
 DO NOT USE FOR: competitor analysis, employee queries, or market questions."
```

**The result:** Tool mis-selection dropped from ~20% to ~8%. The remaining 8% is mostly ambiguous queries where multiple tools could be correct.

**The lesson:** Tool descriptions aren't documentation — they're runtime dispatch instructions. Write them like routing rules, not API docs.

---

## 4. Why SSE Over WebSockets

**The evaluation:** We considered WebSockets for real-time streaming. The agent takes 10-60 seconds per turn, calling multiple tools. Users need to see progress.

**Why we chose SSE:**
- **One-directional fits.** AI streaming is server→client. User input goes via POST. There's no bidirectional requirement.
- **Reconnection is free.** Browsers handle SSE reconnection automatically with `Last-Event-Id`. WebSocket reconnection requires custom logic.
- **Proxy-friendly.** SSE works through corporate proxies and CDNs without special configuration. WebSocket upgrade handshakes fail behind many enterprise proxies.
- **Cost clarity.** Each SSE connection = one turn. Token and cost tracking maps 1:1. WebSocket multiplexing makes attribution harder.

**The one trick:** Heartbeats as SSE comments (`: heartbeat\n\n`). SSE comments are invisible to `EventSource` handlers but reset proxy timeout timers. This solved Cloudflare's 100-second idle timeout without polluting the event stream.

**When we'd switch:** If we added voice input streaming (bidirectional audio + text), WebSockets would become necessary.

---

## 5. Why Composite Tools

**The failure:** "Give me a full briefing on this opportunity" required the agent to call 5 tools sequentially: search → scoring → competitors → staffing → attachments. Each round costs ~$0.15 in tokens (system prompt re-sent each time). Five rounds = $0.75 just for tool overhead, plus 20-30 seconds of latency.

**The fix:** Created two "super-tools" that combine 4-5 individual calls:
- `get_opportunity_intelligence` — opportunity + scoring + competitors + staffing + attachments
- `get_competitor_deep_profile` — company info + awards + hiring signals + OSINT + political connections

**The result:** Common deep-dive queries dropped from 5 rounds to 1. Saved ~$0.60 and ~20 seconds per query.

**The trade-off:** Composite tools return more data than might be needed. For simple queries ("what's the deadline?"), they over-fetch. But the model handles this gracefully — it just ignores the extra data.

**The lesson:** Optimize for the common case. If 60% of queries need the same 5 tools, make that a single tool.

---

## 6. Why the StreamFinalizer Pattern

**The failure:** Early implementation used a single async generator for streaming. It yielded text chunks during iteration, then returned metadata (tool calls, token usage) as the final yield. Problem: the consumer had to special-case the last item. Type safety was lost. And if the consumer broke out of the loop early, the metadata was never collected.

**The fix:** `stream_with_tools()` returns a tuple: `(AsyncIterator[str], StreamFinalizer)`. The iterator handles real-time text delivery. The finalizer is a callable that returns structured metadata after iteration completes.

```python
iterator, finalizer = await provider.stream_with_tools(...)
async for chunk in iterator:
    yield SSEEvent("text", {"content": chunk})
result: StreamResult = await finalizer()  # tool_calls, usage, is_final
```

**The result:** Clean separation of concerns. The streaming consumer never sees metadata. The metadata consumer never deals with streaming. Type safety is maintained at both boundaries.

**The lesson:** When a function produces two different kinds of output (streaming data + structured metadata), don't force them through the same channel.

---

## 7. Why Tier-Based Model Selection

**The failure:** Hardcoded model IDs (`claude-sonnet-4-20250514`) throughout the codebase. When Anthropic released a new model, updating required touching 15+ files. One missed reference caused a surface to use an outdated model for 2 weeks.

**The fix:** Config uses abstract tiers ("fast", "standard", "advanced"). One mapping function resolves tiers to model IDs:

```python
MODEL_TIERS = {
    "fast": "claude-haiku-4-5-20251001",
    "standard": "claude-sonnet-4-20250514",
    "advanced": "claude-opus-4-20250514",
}
```

**The bonus:** This also enabled query-complexity routing. Simple questions (tier 1-2) get the "standard" model even on the War Room surface. Complex questions (tier 3-4) get "advanced." This saves ~60% on model costs for the War Room.

**The lesson:** Never put model IDs in business logic. They're deployment configuration, not application logic.

---

## 8. Why Fire-and-Forget AAR

**The failure:** After-action review (quality diagnostics) ran synchronously after each agent turn. A slow diagnostic (>10s) would delay the user's next interaction. A crashing diagnostic would return a 500 error to the user — for a feature they didn't ask for.

**The fix:** AAR runs on a daemon thread with its own database session and event loop. It has a 30-second timeout. If it fails, it persists a failed record (quality=0) and logs a warning. It can never slow down or crash the user-facing response.

```python
thread = threading.Thread(target=self._run_aar, daemon=True)
thread.start()
# Response already sent to user. Thread runs independently.
```

**The key detail:** `daemon=True` means the thread dies with the main process. No orphan threads. And `get_fresh_session()` ensures it never shares a database connection with the request handler.

**The lesson:** Observability features must be invisible to users. If your monitoring can cause outages, it's not monitoring — it's a liability.

---

## 9. Why Data Gap Disclosure

**The failure:** When a tool returned zero results, the model would fill the gap with hallucinated data. "No competitor data found" became "Based on available information, the main competitors include..." followed by fabricated company names and award amounts.

**The fix:** When a tool returns zero results, inject a mandatory disclosure:

```
[MANDATORY DATA GAP] The search_competitors tool returned no results for this query.
You must not fabricate or assume data for this area.
Acknowledge the gap explicitly in your response.
```

**The result:** Hallucinated data in gap areas dropped from ~35% to <3%. The remaining 3% occurs when the model has partial data from a different tool and extrapolates.

**The lesson:** LLMs don't distinguish between "I have no data" and "I should generate plausible data." You must make the absence of data explicit and frame it as a constraint, not an invitation.

---

## 10. Why Context Compaction Instead of Longer Context

**The alternative:** Use a 200K context model and never worry about context limits.

**Why we didn't:** Cost scales linearly with context length. A 20-tool-round conversation accumulates ~80K tokens of tool results. At Opus pricing, that's ~$1.20/turn in input tokens alone. Most of those tokens are old tool results that the model has already synthesized.

**The fix:** After round 3, estimate total context. If exceeding 30K tokens, truncate old tool results (keep summaries, trim raw data). Keep the most recent 2 rounds in full.

**The result:** Average context stays under 40K tokens even on 15+ round conversations. Cost per turn stays predictable. Quality doesn't noticeably degrade because the model has already processed the truncated data in earlier rounds.

**The lesson:** Large context windows are a safety net, not a strategy. Actively managing context is cheaper and often produces better results because the model focuses on recent, relevant information.
