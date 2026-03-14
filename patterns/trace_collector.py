"""
Trace Collector with Cost Estimation and Anomaly Detection.

See docs/observability.md for full documentation.
"""

import time
from dataclasses import dataclass, field


# Pricing per 1M tokens (as of 2026)
MODEL_PRICES = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-opus-4-20250514": {"input": 15.00, "output": 75.00},
}


@dataclass
class RoundTrace:
    round_num: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    tools_called: list[str]
    duration_seconds: float


@dataclass
class ToolTrace:
    name: str
    input_summary: str
    duration_ms: float
    result_count: int
    error: str | None
    cached: bool


@dataclass
class AgenticTrace:
    """Finalized trace data for persistence."""
    session_id: str
    surface: str
    total_tokens: int
    total_tool_calls: int
    total_duration: float
    hit_max_rounds: bool
    anomalies: list[str]
    estimated_cost: float
    rounds: list[RoundTrace]
    tool_traces: list[ToolTrace]

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


class TraceCollector:
    """
    Per-turn trace collector for agentic AI conversations.

    Records per-round token usage, per-tool execution details,
    and provides cost estimation with prompt caching awareness.
    """

    def __init__(self, session_id: str, surface: str, model: str = "claude-sonnet-4-20250514"):
        self.session_id = session_id
        self.surface = surface
        self.model = model
        self.rounds: list[RoundTrace] = []
        self.tool_traces: list[ToolTrace] = []
        self.start_time = time.time()
        self.max_rounds = 20  # Will be overridden by surface config

    def record_round(self, round_num, usage, tool_calls, duration):
        """Record a completed agentic round."""
        self.rounds.append(RoundTrace(
            round_num=round_num,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            tools_called=[tc.name for tc in tool_calls],
            duration_seconds=duration,
        ))

    def record_tool(self, name, duration_ms, result_count, error, cached, input_summary=""):
        """Record a tool execution."""
        self.tool_traces.append(ToolTrace(
            name=name,
            input_summary=input_summary,
            duration_ms=duration_ms,
            result_count=result_count,
            error=error,
            cached=cached,
        ))

    def finalize(self) -> AgenticTrace:
        """Finalize the trace with computed metrics."""
        total_duration = time.time() - self.start_time
        hit_max = len(self.rounds) >= self.max_rounds

        return AgenticTrace(
            session_id=self.session_id,
            surface=self.surface,
            total_tokens=sum(r.input_tokens + r.output_tokens for r in self.rounds),
            total_tool_calls=len(self.tool_traces),
            total_duration=total_duration,
            hit_max_rounds=hit_max,
            anomalies=self._detect_anomalies(total_duration, hit_max),
            estimated_cost=self._estimate_cost(),
            rounds=self.rounds,
            tool_traces=self.tool_traces,
        )

    def _estimate_cost(self) -> float:
        """
        Estimate cost with prompt caching pricing.

        Anthropic prompt caching creates three pricing tiers:
        - Cache reads: 90% discount (0.1x base input price)
        - Cache writes: 25% surcharge (1.25x base input price)
        - Non-cached input: base price
        - Output: standard price
        """
        prices = MODEL_PRICES.get(self.model, MODEL_PRICES["claude-sonnet-4-20250514"])
        input_price_per_token = prices["input"] / 1_000_000
        output_price_per_token = prices["output"] / 1_000_000

        total = 0.0
        for r in self.rounds:
            # Cache read tokens: 90% discount
            total += r.cache_read_tokens * input_price_per_token * 0.1

            # Cache creation tokens: 25% surcharge
            total += r.cache_creation_tokens * input_price_per_token * 1.25

            # Non-cached input tokens: base price
            non_cached = r.input_tokens - r.cache_read_tokens - r.cache_creation_tokens
            total += max(0, non_cached) * input_price_per_token

            # Output tokens: standard price
            total += r.output_tokens * output_price_per_token

        return round(total, 4)

    def _detect_anomalies(self, total_duration: float, hit_max: bool) -> list[str]:
        """Detect patterns that indicate problems."""
        anomalies = []

        if hit_max:
            anomalies.append(
                "Hit max tool rounds — may indicate insufficient depth or runaway loop"
            )

        if total_duration > 30:
            anomalies.append(
                f"Turn took {total_duration:.1f}s — exceeds 30s threshold"
            )

        empty_tools = [t for t in self.tool_traces if t.result_count == 0]
        if len(empty_tools) > 3:
            anomalies.append(
                f"{len(empty_tools)} tools returned zero results — possible data gap"
            )

        error_tools = [t for t in self.tool_traces if t.error]
        if len(error_tools) > 2:
            anomalies.append(
                f"{len(error_tools)} tool errors — check tool health"
            )

        # Check for slow tools
        slow_tools = [t for t in self.tool_traces if t.duration_ms > 10000]
        if slow_tools:
            names = ", ".join(t.name for t in slow_tools)
            anomalies.append(f"Slow tools (>10s): {names}")

        return anomalies

    async def save(self):
        """
        Fire-and-forget persistence.

        In production, writes to the agentic_traces table and updates
        session-level counters. Failures are logged but never block
        the user-facing response.
        """
        trace = self.finalize()
        # In production: db.add(AgenticTraceRecord(...)); db.commit()
        import logging
        logging.info(
            f"Trace: {trace.total_tokens} tokens, "
            f"{trace.total_tool_calls} tools, "
            f"${trace.estimated_cost:.4f}, "
            f"{trace.total_duration:.1f}s"
        )
