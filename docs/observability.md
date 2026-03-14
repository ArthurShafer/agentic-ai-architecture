# Observability & After-Action Review

Per-turn trace collection, cost estimation with prompt caching awareness, anomaly detection, and background-thread diagnostic review.

## The Problem

Production AI agents are expensive and opaque:
- Token costs vary wildly based on prompt caching hit rates
- Tool calls may silently degrade (returning empty results)
- Long-running turns may hit context limits without warning
- Quality assessment requires human review — or automated diagnostics

## Pattern 1: TraceCollector

A per-turn trace collector that records everything about an agentic conversation turn:

```python
class TraceCollector:
    def __init__(self, session_id, surface):
        self.rounds = []          # Per-round: tokens, tools, duration
        self.tool_traces = []     # Per-tool: name, input, duration, result, cached
        self.start_time = time.time()

    def record_round(self, round_num, usage, tools, duration):
        self.rounds.append(RoundTrace(...))

    def record_tool(self, name, input_summary, duration_ms, result_count, error, cached):
        self.tool_traces.append(ToolTrace(...))

    def finalize(self) -> AgenticTrace:
        return AgenticTrace(
            total_tokens=sum(r.usage.total for r in self.rounds),
            total_tool_calls=len(self.tool_traces),
            total_duration=time.time() - self.start_time,
            hit_max_rounds=len(self.rounds) >= self.max_rounds,
            anomalies=self._detect_anomalies(),
            estimated_cost=self._estimate_cost(),
        )
```

### Cost Estimation with Prompt Caching

Anthropic's prompt caching creates three pricing tiers per request:

| Token Type | Price Modifier |
|-----------|---------------|
| Cache read | 90% discount (0.1x base input price) |
| Cache write | 25% surcharge (1.25x base input price) |
| Non-cached | Base price |

```python
def _estimate_cost(self) -> float:
    total = 0.0
    for round in self.rounds:
        u = round.usage
        # Input: split by cache status
        total += u.cache_read_tokens * MODEL_PRICES[self.model]["input"] * 0.1
        total += u.cache_creation_tokens * MODEL_PRICES[self.model]["input"] * 1.25
        total += (u.input_tokens - u.cache_read_tokens - u.cache_creation_tokens) * MODEL_PRICES[self.model]["input"]
        # Output: standard pricing
        total += u.output_tokens * MODEL_PRICES[self.model]["output"]
    return total
```

### Anomaly Detection

At finalization, check for patterns that indicate problems:

```python
def _detect_anomalies(self) -> list[str]:
    anomalies = []
    if self.hit_max_rounds:
        anomalies.append("Hit max tool rounds — may indicate insufficient tool depth or runaway loop")
    if self.total_duration > 30:
        anomalies.append(f"Turn took {self.total_duration:.1f}s — exceeds 30s threshold")
    empty_tools = [t for t in self.tool_traces if t.result_count == 0]
    if len(empty_tools) > 3:
        anomalies.append(f"{len(empty_tools)} tools returned zero results — possible data gap")
    return anomalies
```

### Fire-and-Forget Persistence

Trace saving never blocks the response:

```python
async def save(self, db):
    try:
        trace = self.finalize()
        db.add(AgenticTraceRecord(
            session_id=self.session_id,
            trace_data=trace.to_dict(),
        ))
        db.commit()
    except Exception as e:
        logger.warning(f"Trace save failed: {e}")
        # Never raise — observability failures must not affect the user
```

## Pattern 2: After-Action Review (AAR)

A background diagnostic that evaluates every agent conversation turn:

```python
def _spawn_aar(self, session_id, messages, trace):
    thread = threading.Thread(
        target=self._run_aar,
        args=(session_id, messages, trace),
        daemon=True  # Dies with main process
    )
    thread.start()

def _run_aar(self, session_id, messages, trace):
    db = get_fresh_session()  # Own DB session
    try:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            asyncio.wait_for(
                run_diagnostic(messages, trace),
                timeout=30.0
            )
        )
        db.add(AARRecord(session_id=session_id, quality=result.score, ...))
        db.commit()
    except asyncio.TimeoutError:
        db.add(AARRecord(session_id=session_id, quality=0, error="timeout"))
        db.commit()
    finally:
        db.close()
```

### Key Design Decisions

1. **Daemon thread** — dies with the main process, no orphan threads
2. **Own DB session** — never shares the request session
3. **Own event loop** — `asyncio.new_event_loop()` since we're on a new thread
4. **30-second timeout** — diagnostics that take longer than this are broken
5. **Always persists** — even on timeout/error, a record is created with `quality=0`

### Evidence Capture

Raw evidence is captured regardless of diagnostic success:

```python
@dataclass
class AAREvidence:
    user_query: str
    tools_called: list[str]
    tool_results_summary: dict
    model_response_length: int
    response_text: str  # Full response for quality grading
    trace_summary: dict  # From TraceCollector
```

This enables offline analysis: "Why did the agent score poorly on this query?" can be answered by reviewing the evidence without replaying the interaction.
