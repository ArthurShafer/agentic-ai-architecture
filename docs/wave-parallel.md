# Wave-Based Parallel Generation

Queue-multiplexed parallel section generation with dependency ordering — for multi-section documents where later sections build on earlier ones.

## The Problem

Generating a 7-section intelligence report sequentially takes 3-5 minutes. But sections have dependencies: the Executive Summary needs to see all other sections, and strategy sections build on competitive analysis.

Naive parallelism (run all sections concurrently) produces incoherent output because later sections lack context from earlier ones. Full sequential generation wastes time on independent sections.

## The Pattern: Dependency Waves

Group sections into waves based on their data dependencies:

```
Wave 1 (parallel):  Requirements Analysis  ║  Positioning Strategy
                            ↓                        ↓
Wave 2 (parallel):  Competitive Landscape  ║  Winning Strategy
                            ↓                        ↓
Wave 3 (sequential): Risk Assessment → Action Plan
                            ↓
Wave 4 (sequential): Executive Summary (sees ALL prior sections)
```

- Sections within a wave run **concurrently**
- Waves run **sequentially** — wave N+1 starts only after wave N completes
- Later waves receive `prior_summaries` from completed waves
- The final section (Executive Summary) receives `full_sections` for comprehensive synthesis

## Queue-Multiplexed Streaming

Each section gets its own `asyncio.Queue`. The main generator polls all active queues and yields SSE events:

```python
async def generate_brief(opportunity, config):
    for wave in WAVE_ORDER:
        queues = {}
        tasks = []

        for section in wave.sections:
            queue = asyncio.Queue()
            queues[section.name] = queue
            task = asyncio.create_task(
                generate_section(section, queue, prior_summaries)
            )
            tasks.append(task)

        # Poll-drain all queues, yielding SSE events
        active = set(queues.keys())
        while active:
            for name in list(active):
                try:
                    event = queues[name].get_nowait()
                    if event is _SECTION_DONE:
                        active.discard(name)
                    else:
                        yield event  # SSE to client
                except asyncio.QueueEmpty:
                    pass
            await asyncio.sleep(0.05)  # 50ms poll interval

        # Generate summaries for next wave
        for section in wave.sections:
            prior_summaries[section.name] = await summarize(
                section.output, model="fast"
            )
```

### The Sentinel Object Pattern

A `_SECTION_DONE = object()` sentinel signals completion:

```python
async def generate_section(section, queue, prior_summaries):
    try:
        async for chunk in run_agentic_section(section, prior_summaries):
            await queue.put(SSEEvent("text", chunk))
    finally:
        await queue.put(_SECTION_DONE)  # ALWAYS pushed, even on error
```

The `finally` block ensures `_SECTION_DONE` is always pushed, preventing deadlocks if a section errors out.

### Cost-Efficient Summaries

Inter-wave summaries use the cheapest model (Haiku) with a 2-sentence limit:

```python
async def summarize(section_text: str, model: str = "fast") -> str:
    return await llm.complete(
        f"Summarize in exactly 2 sentences: {section_text}",
        model=get_model_for_tier(model),
        max_tokens=100
    )
```

This keeps the context compact for downstream sections while preserving key information.

## Performance Impact

| Approach | Duration | Cost |
|----------|----------|------|
| Sequential (7 sections) | ~4 min | Baseline |
| Wave-parallel (4 waves) | ~2.5 min | +5% (summary calls) |

The ~35% time reduction comes from parallelizing independent sections within each wave.
