# Design Decisions — Lessons from Production

Every pattern in this repo exists because something broke, cost too much, or confused users in production. This document captures the "why" behind each decision.

**Reading guide:** Decisions are grouped by domain. Each follows the same format: what failed → what we changed → what we measured → what we learned.

---

## Prompt Engineering & LLM Behavior

### 1. Data Gap Disclosure Prevents Hallucination

**The failure:** When a tool returned zero results, the model would fill the gap with plausible-sounding fabrications. "No competitor data found" silently became "Based on available information, the main competitors include Lockheed Martin and Raytheon..." — with fabricated award amounts, fabricated win rates, and fabricated analysis. Users trusted it because the rest of the response was data-backed.

**The fix:** When any tool returns zero results, inject a mandatory constraint into the next turn:

```
[MANDATORY DATA GAP] 2 tool(s) returned ZERO results this round: search_competitors, get_awards.
CRITICAL INSTRUCTION: You MUST NOT present data for these tools as if you have current results.
For each tool that returned empty, you MUST include '[DATA GAP]' in your response.
Do NOT use data from earlier conversation turns to fill this gap.
VIOLATION: Presenting detailed analysis from prior turns when the current tool returned
empty is a hallucination. Say '[DATA GAP]' and move on.
```

**The key insight:** The model wasn't hallucinating because it wanted to — it was filling gaps because the prompt didn't distinguish between "I have no data" and "I should generate plausible content." Making absence explicit and framing it as a *constraint* rather than a gap to fill changed the behavior completely.

**Escalation pattern:** After 3+ cumulative empty returns from the same tool, a stronger message fires: "This tool has been tried multiple times without results. Accept the gap and move on." This prevents the model from re-calling the same empty tool with rephrased queries.

**The result:** Hallucinated data in zero-result areas dropped from ~35% to <3%. The remaining 3% occurs when the model has partial data from a different tool and extrapolates.

**The lesson:** LLMs don't distinguish between "no data" and "opportunity to generate." You must make absence explicit and frame it as a hard constraint. The word "VIOLATION" in the prompt matters — it triggers stronger compliance than softer phrasing like "please avoid."

---

### 2. Surface-Specific Prompt Personalities From One Engine

**The problem:** The same query — "how many opportunities are in proposal stage?" — needs a completely different response depending on where the user asks it:

- In the **sidebar**: "**12 opportunities** in proposal stage. 8 active, 4 awaiting eval."  (150 words max)
- In the **briefing room**: A 2,000-word strategic analysis with pipeline velocity metrics, competitive positioning, staffing readiness, and risk factors.

Building separate agents for each surface duplicates code and diverges over time.

**The fix:** One engine, multiple prompt personalities. The system prompt includes a surface-specific block:

```
# Sidebar
CRITICAL OUTPUT RULES FOR SIDEBAR:
- Maximum 150 words. Lead with the direct answer in the FIRST sentence.
- Use bullet points, not paragraphs.
- Do NOT narrate your process (no 'Let me search...', 'I found...').
- If the query needs deeper analysis, end with: 'For deeper analysis, try the War Room.'

# War Room
You are in the War Room command center. Full analytical depth is expected.
Do not self-truncate when you have more insights to deliver.
But every paragraph must earn its place. If you find yourself summarizing
what you just said, stop. The goal is insight density, not word count.
```

**The before/after is dramatic:** Same model, same tools, same data — a 10x difference in output length and depth, controlled entirely through prompt phrasing.

**The lesson:** The model follows surface rules more reliably than you'd expect, *if* the rules are concrete ("Maximum 150 words", not "Be concise"). Quantitative constraints in prompts outperform qualitative ones.

---

### 3. Tier-Classified Output Rules (Zero-Cost Query Routing)

**The failure:** Every query got the same treatment. "What's the deadline for DCGS?" burned $0.85 in Opus tokens for a one-sentence answer. "Build me a full competitive strategy" got a 3-paragraph response because the model self-truncated.

**The fix:** A regex-based classifier (no LLM call, <1ms) pre-classifies every query into tiers 1-5 and injects tier-specific output rules:

```python
TIER_OUTPUT_RULES = {
    1: "QUERY TIER: Simple Retrieval. Answer in 1-3 sentences. Max 100 words. "
       "Use at most 2 tool calls. Do NOT provide unsolicited analysis.",

    3: "QUERY TIER: Cross-Domain Synthesis. Full analysis expected. Max 500 words. "
       "Use at most 10 tool calls. Connect insights across 2-4 data domains.",

    4: "QUERY TIER: Strategic Synthesis. Comprehensive strategic briefing expected. "
       "Up to 15 tool calls. Include action items and confidence levels.",
}
```

The tier also controls model selection: Tiers 1-2 get the standard model ($3/M input), Tiers 3-4 get the advanced model ($15/M input). Simple queries never touch the expensive model.

**Why regex, not an LLM classifier?** An LLM classification call costs ~$0.002 and adds 300ms latency. The regex classifier runs in <1ms and catches 90%+ of cases correctly. The 10% it misclassifies still get a reasonable response — just with slightly wrong depth. The cost savings pay for the edge cases.

**The result:** ~60% reduction in model costs on the full-depth surface, with no user-perceived quality loss.

**The lesson:** Not everything needs an LLM. The cheapest, fastest solution is often a pile of regexes — and for query classification, it's good enough.

---

### 4. Intelligence Fusion Map (Teaching Cross-Domain Reasoning)

**The failure:** The model would answer questions within a single domain competently but never *combined* insights. "Should we bid on this?" would search opportunities and give a factual summary. It wouldn't cross-reference competitor positioning, check our past performance in the agency, look at staffing availability, or assess whether the timeline conflicts with other pursuits.

**The fix:** The system prompt includes an explicit "fusion map" — a menu of cross-domain combinations with descriptions of what each intersection reveals:

```
INTELLIGENCE FUSION MAP: Before synthesizing, consider which intersections are relevant.

- Evaluation history + competitor → Where competitors have been penalized or praised
- Contract history + opportunity → Incumbency map; who holds predecessor work
- Hiring signals + opportunity → Competitor mobilization; are they building a team?
- Prediction signals + amendments → Timeline confidence; are deadline slips likely?
- Our awards + requirements → Past performance alignment vs. gaps
- Pipeline data + staffing → Can we actually staff this if we win?

The power is in the intersection. A single data point is a fact.
Two corroborating signals from different domains are intelligence.
Three are a strategic finding.
```

**The result:** Multi-tool usage on strategic queries increased from an average of 2.3 tools to 5.1 tools. More importantly, responses started surfacing non-obvious connections — "Company X is hiring in the same geography as this opportunity, suggesting they're mobilizing to bid."

**The lesson:** LLMs can reason across domains, but they won't do it spontaneously. You have to teach them what intersections exist and why they matter. The fusion map is essentially a curriculum for cross-domain reasoning.

---

### 5. Short-Response Rescue Mechanism

**The failure:** The model would call 8 tools, accumulate rich data across competitors, awards, staffing, and predictions... then produce a 3-sentence summary. "Based on my analysis, this opportunity looks promising. The competitive landscape is moderate. I recommend proceeding."

The tool results appeared in a sidebar the user barely reads. The model's text response was the only deliverable — and it was empty.

**Why this happens:** The model processes tool results as they arrive and builds an internal understanding. By the time it generates its response, it "feels" like it has communicated the analysis — because it has, to itself. It confuses processing with communicating.

**The fix:** After the agentic loop completes, check if the streamed text is disproportionately short relative to tools called:

```python
# Thresholds calibrated from production data
MIN_CHARS = {(3, 5): 1200, (6, 10): 2500, (11, float('inf')): 4000}

if visible_text_length < min_threshold:
    inject_rescue_prompt(
        f"Your response was only {visible_text_length} characters after using "
        f"{tools_called} tools. The user cannot see tool results in the sidebar. "
        f"You MUST now write a comprehensive analysis including specific findings, "
        f"data points, and recommendations from your tool results. "
        f"Write at least {min_chars} characters of substantive analysis."
    )
    # Force one more LLM turn with no tools available
```

**The key phrase:** "The user cannot see tool results in the sidebar." This reframes the model's understanding — it thought the work was done because the data existed somewhere in the conversation. Telling it explicitly that the user can't see that data forces it to externalize its analysis.

**The result:** Rescue triggers on ~12% of turns. When it fires, the re-synthesized response is consistently rated higher quality than responses that met the threshold on the first pass — because the model has already processed all the data and can write a more integrated synthesis.

**The lesson:** Models confuse internal processing with external communication. Always verify that the *user-visible* output matches the *work performed*. And when it doesn't, a programmatic backstop is more reliable than a prompt instruction.

---

### 6. Prompt Caching Strategy (System Prompt Architecture)

**The problem:** With a 27-tool agent, the system prompt alone is ~6,000 tokens. On a 10-round agentic conversation, that's 60,000 tokens of repeated system prompt — ~$0.90 at Opus pricing just for re-sending the same instructions.

**The fix:** Wrap the system prompt with `cache_control` for Anthropic's prompt caching:

```python
kwargs["system"] = [{
    "type": "text",
    "text": system_prompt,
    "cache_control": {"type": "ephemeral"},
}]
```

Round 1 pays a 25% surcharge for cache creation. Rounds 2-20 get a 90% discount on the system prompt. The break-even point is round 2 — every conversation with 2+ rounds saves money.

**The system prompt is assembled from 12 modular components:** Soul identity, temporal header, tool usage instructions, data availability discipline, intelligence fusion map, executive reasoning framework, temporal reasoning rules, cross-domain synthesis rules, output rules, tool strategy guidance, strategic intelligence questions, and surface-specific directives. Dynamic context (user profile, active widgets, episodic memory) is appended *after* the cached block so it doesn't invalidate the cache.

**The result:** System prompt token costs dropped ~85% on multi-round conversations. Average turn went from ~$0.15 to ~$0.04 for the system prompt portion.

**The lesson:** In agentic systems, the system prompt is the most cacheable thing you have — it doesn't change between rounds. Structure your prompts so the static parts come first (and are cached) and the dynamic parts come last. This is a direct consequence of how prefix-based caching works: any change invalidates everything after it.

---

## Agent Infrastructure

### 7. Fresh DB Sessions Per Tool Call

**The failure:** During an agentic loop, Tool A ran a query that threw a database error. SQLAlchemy rolled back the session. Tool B then tried to query using the same session — but the ORM's identity map was corrupted. Tool B returned stale/wrong data. The LLM synthesized a confident, incorrect answer. The user made a business decision based on fabricated data.

**The fix:** Every tool call creates its own database session, used only for that tool, and closed in a `finally` block regardless of outcome.

```python
async def execute_tool(tool_name, tool_input, ...):
    tool_db = get_session()  # Fresh session — isolated from all other tools
    try:
        result = await handler(db=tool_db, **kwargs)
        return sanitize(result)
    except Exception:
        tool_db.rollback()
        raise
    finally:
        tool_db.close()  # Always close. Always.
```

**The cost:** ~2ms overhead per tool call. Negligible compared to the LLM round-trip (~2-15 seconds).

**The lesson:** In agentic systems, tools share conversational state through the LLM, but they must NOT share infrastructure state. A tool failure should be invisible to other tools.

---

### 8. Budget Awareness Injection

**The failure:** The agent would call 15+ tools on a simple question, burning $2-3 in tokens. On complex questions, it would stop after 2 tools because the first results looked "good enough."

**The paper:** Inspired by research on token-aware agent steering (arXiv:2511.17006). LLMs have no inherent sense of budget — they don't know how many rounds they have left or how many tokens they've consumed.

**The fix:** After every tool round, inject a text message with explicit remaining capacity:

```python
budget_msg = f"[Budget: round {round_num + 1}/{max_rounds} used, {tokens:,} tokens consumed]"
if remaining <= 1:
    budget_msg += " FINAL ROUND: synthesize your findings now."
elif remaining <= 3:
    budget_msg += f" {remaining} rounds remaining - begin converging."
```

**The result:** Tool usage dropped ~40% on simple queries. Complex queries now use their full budget instead of stopping early. Cost per query became predictable.

**The lesson:** Don't rely on the model to self-regulate. Give it explicit, quantitative constraints in natural language. "You have 3 rounds remaining" is more effective than "be efficient."

---

### 9. USE WHEN / DO NOT USE Tool Annotations

**The failure:** With 27 tools, the model frequently picked the wrong one. "Tell me about Lockheed Martin" triggered `search_opportunities` instead of `get_competitor_profile`. Irrelevant results, wasted rounds, apologetic responses.

**The fix:** Explicit dispatch guidance in tool descriptions — written like routing rules, not API docs:

```
Search competitive intelligence for LANDSCAPE-LEVEL competitor summaries.
USE WHEN: Comparing multiple competitors, identifying who's active in an agency/NAICS.
DO NOT USE FOR: Detailed contract history — use get_competitor_contracts.
DO NOT USE FOR: Financial data — use query_sec_filings.
DO NOT USE FOR: Hiring patterns — use query_hiring_intelligence.
ESCALATION: If 0 results, try traverse_relationships before reporting 'no data found.'
```

**The "ESCALATION" instruction** was a later addition. Without it, the model would get zero results and immediately report "no competitor data available" — even though alternative tools existed. With it, the model tries a fallback tool first.

**The result:** Mis-selection dropped from ~20% to ~8%.

**The lesson:** Tool descriptions aren't documentation for humans — they're runtime dispatch instructions for the model. Write them like if/else routing rules with explicit fallback paths.

---

### 10. Composite Tools (Amortizing Round-Trip Costs)

**The failure:** "Brief me on this opportunity" required 5 sequential tool calls: search → scoring → competitors → staffing → attachments. Each round re-sends the system prompt (~6K tokens) plus all accumulated context. Five rounds = $0.75 in overhead alone, plus 20-30 seconds of latency.

**The fix:** Two "super-tools" combine 4-5 individual calls into one:
- `get_opportunity_intelligence` — opportunity + scoring + competitors + staffing + attachments
- `get_competitor_deep_profile` — company info + awards + hiring signals + OSINT + relationships

```python
async def get_opportunity_intelligence(notice_id: str, db):
    """One tool call replaces 5 sequential rounds."""
    opp = await get_opportunity(notice_id, db)
    scores = await get_scoring(notice_id, db)
    competitors = await find_competitors(notice_id, db)
    staffing = await match_employees(notice_id, db)
    return ToolResult(summary=combine(opp, scores, competitors, staffing), ...)
```

**The result:** Common deep-dive queries dropped from 5 rounds to 1. Saved ~$0.60 and ~20 seconds per query.

**The trade-off:** Composite tools over-fetch for simple queries ("what's the deadline?"). But the model handles this gracefully — it ignores irrelevant sections.

**The lesson:** In agentic systems, the marginal cost of a tool call is dominated by the LLM round-trip, not the tool execution. Reducing round-trips is the highest-leverage optimization.

---

### 11. The StreamFinalizer Pattern

**The failure:** Early implementation used a single async generator for streaming. It yielded text chunks, then returned metadata (tool calls, usage) as the final yield. The consumer had to special-case the last item. Type safety was lost. If the consumer broke early, metadata was never collected.

**The fix:** `stream_with_tools()` returns a tuple: `(AsyncIterator[str], StreamFinalizer)`:

```python
iterator, finalizer = await provider.stream_with_tools(...)

async for chunk in iterator:
    yield SSEEvent("text", {"content": chunk})  # Real-time to user

result: StreamResult = await finalizer()  # Structured metadata after iteration
# result.tool_calls, result.usage, result.is_final
```

**The lesson:** When a function produces two different kinds of output (streaming data + structured metadata), don't force them through the same channel. Separate the concerns at the API boundary.

---

### 12. Fire-and-Forget AAR (Observability That Can't Break Things)

**The failure:** After-action review (quality diagnostics) ran synchronously after each turn. A slow diagnostic delayed the user's next interaction. A crashing diagnostic returned a 500 error for a feature the user never asked for.

**The fix:** AAR runs on a daemon thread with its own database session and event loop:

```python
thread = threading.Thread(target=self._run_aar, daemon=True)
thread.start()  # Response already sent. Thread is independent.
```

Three isolation mechanisms:
1. **Daemon thread** — dies with the main process, no orphans
2. **Own DB session** — `get_fresh_session()`, never shares with request handler
3. **30-second timeout** — diagnostics that take longer are broken, not slow

If it fails, it persists a record with `quality=0` and logs a warning. It can *never* affect the user.

**The lesson:** Observability must be invisible to users. If your monitoring can cause outages, it's not monitoring — it's a liability.

---

### 13. Context Compaction Over Longer Context Windows

**The alternative:** Use a 200K context model and never worry about limits.

**Why we didn't:** Cost scales linearly with context length. A 20-round conversation accumulates ~80K tokens of tool results. At Opus pricing, that's ~$1.20/turn in input tokens alone. Most of those tokens are old tool results the model has already synthesized.

**The fix:** After round 3, estimate total context. If exceeding 30K tokens, truncate old tool results (keep summaries, trim raw data). Keep the 2 most recent rounds in full.

**The result:** Average context stays under 40K tokens even on 15+ round conversations. Quality doesn't noticeably degrade because the model already processed the truncated data in earlier rounds.

**The lesson:** Large context windows are a safety net, not a strategy. Active context management is cheaper and often produces better results — the model focuses on recent, relevant information instead of swimming through 80K tokens of historical tool dumps.

---

## Cost Impact Summary

These decisions aren't theoretical — they have measurable production impact:

| Decision | Annual Savings (est.) | How |
|----------|----------------------|-----|
| Tier-based model routing | ~60% model cost reduction | Simple queries never touch Opus |
| Prompt caching | ~85% system prompt cost reduction | Cache hit on rounds 2-20 |
| Composite tools | ~$0.60 saved per deep-dive | 5 rounds → 1 round |
| Budget awareness | ~40% fewer tool calls on simple queries | Models stop when they have enough |
| Context compaction | ~$0.80 saved per long conversation | 80K tokens → 40K tokens |
| Diminishing returns detection | ~2-3 rounds saved per complex query | Stop redundant tool calls |

The combined effect: an average query costs ~$0.15 instead of ~$0.85 — an **82% reduction** from the naive "call Opus with all tools and let it run" approach.

---

## Cross-Reference

Each decision maps to a pattern implementation:

| Decision | Pattern File | Architecture Doc |
|----------|-------------|-----------------|
| Data gap disclosure | [`patterns/agentic_loop.py`](patterns/agentic_loop.py) | [`docs/agentic-loop.md`](docs/agentic-loop.md) |
| Surface-specific prompts | [`patterns/agentic_loop.py`](patterns/agentic_loop.py) | [`docs/surface-config.md`](docs/surface-config.md) |
| Tier classification | [`patterns/agentic_loop.py`](patterns/agentic_loop.py) | [`docs/surface-config.md`](docs/surface-config.md) |
| Prompt caching | [`patterns/llm_provider.py`](patterns/llm_provider.py) | [`docs/llm-abstraction.md`](docs/llm-abstraction.md) |
| Fresh DB sessions | [`patterns/tool_dispatch.py`](patterns/tool_dispatch.py) | [`docs/tool-dispatch.md`](docs/tool-dispatch.md) |
| Budget awareness | [`patterns/agentic_loop.py`](patterns/agentic_loop.py) | [`docs/agentic-loop.md`](docs/agentic-loop.md) |
| Tool annotations | [`patterns/tool_dispatch.py`](patterns/tool_dispatch.py) | [`docs/tool-dispatch.md`](docs/tool-dispatch.md) |
| Composite tools | [`patterns/tool_dispatch.py`](patterns/tool_dispatch.py) | [`docs/tool-dispatch.md`](docs/tool-dispatch.md) |
| StreamFinalizer | [`patterns/llm_provider.py`](patterns/llm_provider.py) | [`docs/llm-abstraction.md`](docs/llm-abstraction.md) |
| Fire-and-forget AAR | [`patterns/trace_collector.py`](patterns/trace_collector.py) | [`docs/observability.md`](docs/observability.md) |
| Context compaction | [`patterns/agentic_loop.py`](patterns/agentic_loop.py) | [`docs/agentic-loop.md`](docs/agentic-loop.md) |
| Cost estimation | [`patterns/trace_collector.py`](patterns/trace_collector.py) | [`docs/observability.md`](docs/observability.md) |
