# System Architecture Diagrams

## Full System Overview

```
                                    ┌─────────────────────────────┐
                                    │        React SPA            │
                                    │  ┌───────┐ ┌────────────┐  │
                                    │  │Sidebar│ │  War Room   │  │
                                    │  │ Chat  │ │  Briefing   │  │
                                    │  └───┬───┘ └─────┬──────┘  │
                                    │      │           │         │
                                    │  ┌───┴───────────┴──────┐  │
                                    │  │  SSE Event Handlers   │  │
                                    │  │  text | tool_start |  │  │
                                    │  │  tool_result | done   │  │
                                    │  └───────────┬──────────┘  │
                                    └──────────────┼─────────────┘
                                                   │
                                          POST /chat/stream
                                          SSE response ↑↓
                                                   │
┌──────────────────────────────────────────────────┼──────────────────────────────┐
│  FastAPI Backend                                 │                              │
│                                                  │                              │
│  ┌───────────────────────────────────────────────▼───────────────────────────┐  │
│  │                        Surface Router                                     │  │
│  │                                                                           │  │
│  │   Query ──► Classify Complexity (Tiers 1-5) ──► Select Surface Config     │  │
│  │                                                                           │  │
│  │   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                  │  │
│  │   │  Sidebar     │    │  Opp Chat   │    │  War Room   │                  │  │
│  │   │  standard    │    │  standard   │    │  advanced   │                  │  │
│  │   │  3 rounds    │    │  3 rounds   │    │  20 rounds  │                  │  │
│  │   │  6 tools     │    │  8 tools    │    │  27 tools   │                  │  │
│  │   └──────┬──────┘    └──────┬──────┘    └──────┬──────┘                  │  │
│  │          │ nudge ──────────►│ nudge ──────────►│                          │  │
│  └──────────┼──────────────────┼──────────────────┼──────────────────────────┘  │
│             └──────────────────┴──────────────────┘                              │
│                                    │                                             │
│  ┌─────────────────────────────────▼─────────────────────────────────────────┐  │
│  │                         Agentic Loop Engine                                │  │
│  │                                                                            │  │
│  │   ┌──────────────────────────────────────────────────────────────────┐     │  │
│  │   │  for round in range(max_rounds):                                 │     │  │
│  │   │    Stream LLM ──► Collect text + tool_use blocks                 │     │  │
│  │   │    if is_final: break                                            │     │  │
│  │   │    Execute tools (parallel-safe, timeout per tool)               │     │  │
│  │   │    ┌────────────────────────────────────────────────────────┐    │     │  │
│  │   │    │ Post-Round Pipeline:                                   │    │     │  │
│  │   │    │  ► Budget awareness injection                          │    │     │  │
│  │   │    │  ► Data gap disclosure (zero-result tools)             │    │     │  │
│  │   │    │  ► Context compaction (if >30K tokens)                 │    │     │  │
│  │   │    │  ► Diminishing returns detection                       │    │     │  │
│  │   │    │  ► Circuit breaker (3 consecutive errors)              │    │     │  │
│  │   │    └────────────────────────────────────────────────────────┘    │     │  │
│  │   │  Max rounds ──► Forced synthesis turn                           │     │  │
│  │   │  Short response ──► Rescue re-synthesis                         │     │  │
│  │   └──────────────────────────────────────────────────────────────────┘     │  │
│  └──────────────┬────────────────────────────────────────────────────────────┘  │
│                 │                                                                │
│  ┌──────────────▼────────────────────────────────────────────────────────────┐  │
│  │                         Tool Dispatch Layer                                │  │
│  │                                                                            │  │
│  │   ┌────────────┐    ┌────────────┐    ┌────────────────────────────────┐   │  │
│  │   │  Registry   │    │  Dispatch   │    │  Execution Pipeline           │   │  │
│  │   │             │    │  Table      │    │                               │   │  │
│  │   │  27 schemas │──►│  name ──►   │──►│  1. Cache check               │   │  │
│  │   │  USE WHEN   │    │  handler    │    │  2. Fresh DB session          │   │  │
│  │   │  DO NOT USE │    │  (lazy      │    │  3. Dynamic signature inspect │   │  │
│  │   │             │    │   imports)  │    │  4. Execute handler           │   │  │
│  │   └────────────┘    └────────────┘    │  5. Sanitize + redact PII     │   │  │
│  │                                        │  6. CRAG (retrieval tools)    │   │  │
│  │                                        └────────────────────────────────┘   │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌────────────────────────┐  ┌────────────────────────┐  ┌──────────────────┐  │
│  │  LLM Provider Layer    │  │  Trace Collector        │  │  AAR (daemon     │  │
│  │                        │  │                         │  │   thread)        │  │
│  │  ABC ──► Anthropic     │  │  Per-round: tokens,     │  │                  │  │
│  │  StreamFinalizer       │  │   tools, duration       │  │  Own DB session  │  │
│  │  Tier ──► Model ID     │  │  Cost estimation w/     │  │  30s timeout     │  │
│  │  Prompt cache control  │  │   cache pricing         │  │  Fire-and-forget │  │
│  │                        │  │  Anomaly detection      │  │  Quality scoring │  │
│  └────────────────────────┘  └────────────────────────┘  └──────────────────┘  │
│                                                                                  │
└───────────────┬──────────────────────┬───────────────────────┬──────────────────┘
                │                      │                       │
       ┌────────▼────────┐    ┌───────▼────────┐    ┌────────▼────────┐
       │   PostgreSQL     │    │     Redis       │    │   Vector DB     │
       │   (primary)      │    │  (cache/queue)  │    │  (embeddings)   │
       └─────────────────┘    └────────────────┘    └─────────────────┘
```

## Agentic Loop Flow

```
          User Query
              │
              ▼
    ┌─────────────────┐
    │ Classify         │
    │ Complexity       │──── Tier 1-2, 5 ──► Standard Model ($)
    │ (Tiers 1-5)     │──── Tier 3-4    ──► Advanced Model ($$$)
    └────────┬────────┘
             │
             ▼
    ╔═══════════════════════════════════════════════╗
    ║            AGENTIC LOOP                       ║
    ║                                               ║
    ║   Round 1  ┌────────────────────┐             ║
    ║   ───────► │ Stream LLM         │             ║
    ║            │ response           │             ║
    ║            └────────┬───────────┘             ║
    ║                     │                         ║
    ║              ┌──────▼──────┐                  ║
    ║              │ Tool calls? │                  ║
    ║              └──────┬──────┘                  ║
    ║                YES  │  NO ──► Final Response  ║
    ║                     │                         ║
    ║            ┌────────▼────────┐                ║
    ║            │ Execute Tools    │                ║
    ║            │ (with timeout)   │                ║
    ║            └────────┬────────┘                ║
    ║                     │                         ║
    ║            ┌────────▼────────┐                ║
    ║            │ 3 consecutive   │                ║
    ║            │ errors?         │ YES ──► BREAK  ║
    ║            └────────┬────────┘   (circuit     ║
    ║                NO   │             breaker)    ║
    ║                     │                         ║
    ║   ┌─────────────────▼──────────────────┐      ║
    ║   │ Post-Round Pipeline                 │      ║
    ║   │                                     │      ║
    ║   │ "You have 4 rounds remaining"       │ ◄── Budget
    ║   │                                     │      Awareness
    ║   │ "[DATA GAP] search returned 0"      │ ◄── Gap
    ║   │                                     │      Disclosure
    ║   │ Truncate old results if >30K tokens │ ◄── Context
    ║   │                                     │      Compaction
    ║   │ <5 new terms for 2 rounds?          │ ◄── Diminishing
    ║   │  ──► "Consider synthesizing"        │      Returns
    ║   └─────────────────┬──────────────────┘      ║
    ║                     │                         ║
    ║   Round 2  ┌────────▼───────┐                 ║
    ║   ───────► │ Stream LLM ... │ (repeat)        ║
    ║            └────────────────┘                  ║
    ║                                               ║
    ║   Max rounds hit ──► Forced synthesis turn    ║
    ╚═══════════════════════════════════════════════╝
              │
              ▼
    ┌─────────────────┐
    │ Response < 1200  │ YES ──► Rescue re-synthesis
    │ chars after 3+   │         "Provide a more
    │ tools?           │          comprehensive answer"
    └────────┬────────┘
             │ NO
             ▼
    ┌─────────────────┐     ┌──────────────────┐
    │ Emit SSE: done  │     │ Spawn AAR thread  │
    │ {tokens, tools} │     │ (fire-and-forget) │
    └─────────────────┘     └──────────────────┘
```

## Tool Dispatch Pipeline

```
    Tool Call from LLM
    {name: "search_competitors", input: {query: "Lockheed Martin"}}
         │
         ▼
    ┌─────────────┐
    │ Cache Check  │──── HIT ──► Return cached result
    └──────┬──────┘
           │ MISS
           ▼
    ┌─────────────────┐
    │ Fresh DB Session │ ◄── Prevents session poisoning
    │ (isolated)       │     between tool calls
    └──────┬──────────┘
           │
           ▼
    ┌─────────────────────┐
    │ Signature Inspection │
    │                      │
    │ inspect.signature()  │
    │ Only pass params the │
    │ handler accepts      │
    └──────┬──────────────┘
           │
           ▼
    ┌─────────────────┐
    │ Execute Handler  │──── ERROR ──► db.rollback()
    └──────┬──────────┘              db.close()
           │ SUCCESS                  raise
           ▼
    ┌──────────────────┐
    │ Sanitize Output   │
    │                   │
    │ sanitize_for_llm()│ ◄── Strip prompt injection
    │ redact_pii()      │ ◄── Mask SSNs, emails, phones
    └──────┬───────────┘
           │
           ▼
    ┌──────────────────┐
    │ Is retrieval tool?│ NO ──► Return result
    └──────┬───────────┘
           │ YES
           ▼
    ┌──────────────────────────────────────┐
    │ CRAG (Corrective RAG)                │
    │                                      │
    │ Grade relevance (Haiku):             │
    │  RELEVANT ──► Return as-is           │
    │  INSUFFICIENT ──► Rewrite query ──►  │
    │                   Re-retrieve        │
    │                   (max 2 retries)    │
    │  OFF_TOPIC ──► Return empty + gap    │
    └──────────────────────────────────────┘
```

## Wave-Based Parallel Generation

```
    Intelligence Brief Generation
              │
              ▼
    ┌─────────────────────────────────┐
    │ Pre-flight Enrichment            │
    │ (fire-and-forget)                │
    │                                  │
    │ ► Document analysis (if stale)   │
    │ ► Competitive matching           │
    │ ► Staffing matching              │
    └────────────┬────────────────────┘
                 │
                 ▼
    ╔════════════════════════════════════════════════╗
    ║  Wave 1 (parallel)                             ║
    ║  ┌──────────────────┐  ┌────────────────────┐  ║
    ║  │ Requirements     │  │ Positioning        │  ║
    ║  │ Analysis         │  │ Strategy           │  ║
    ║  │ [own Queue]      │  │ [own Queue]        │  ║
    ║  │ [own tools]      │  │ [own tools]        │  ║
    ║  └────────┬─────────┘  └────────┬───────────┘  ║
    ║           │                     │               ║
    ║     ┌─────▼─────────────────────▼─────┐         ║
    ║     │ Poll-drain queues (50ms)         │──► SSE  ║
    ║     │ Yield events as they arrive      │         ║
    ║     └─────────────────────────────────┘         ║
    ╚════════════════╤═══════════════════════════════╝
                     │ summarize (Haiku, 2 sentences)
                     ▼
    ╔════════════════════════════════════════════════╗
    ║  Wave 2 (parallel)                             ║
    ║  ┌──────────────────┐  ┌────────────────────┐  ║
    ║  │ Competitive      │  │ Winning            │  ║
    ║  │ Landscape        │  │ Strategy           │  ║
    ║  │ [prior_summaries]│  │ [prior_summaries]  │  ║
    ║  └────────┬─────────┘  └────────┬───────────┘  ║
    ╚═══════════╤═════════════════════╤══════════════╝
                │ summarize           │
                ▼                     ▼
    ╔════════════════════════════════════════════════╗
    ║  Wave 3 (sequential)                           ║
    ║  Risk Assessment ──► Action Plan               ║
    ╚════════════════╤═══════════════════════════════╝
                     │
                     ▼
    ╔════════════════════════════════════════════════╗
    ║  Wave 4 (sequential)                           ║
    ║  Executive Summary                             ║
    ║  [receives full_sections from all prior waves] ║
    ╚════════════════════════════════════════════════╝
```

## SSE Event Timeline

```
    Client                          Server
      │                               │
      │──── POST /chat/stream ───────►│
      │                               │
      │◄─── event: session ───────────│  {"session_id": "abc-123"}
      │                               │
      │◄─── event: text ─────────────│  {"content": "Let me analyze "}
      │◄─── event: text ─────────────│  {"content": "the competitive..."}
      │                               │
      │                               │  [Tool execution begins]
      │◄─── event: tool_start ───────│  {"tool": "search_competitors"}
      │                               │
      │     ... 8 seconds pass ...     │  [Tool running]
      │◄─── : heartbeat ─────────────│  [SSE comment, invisible to JS]
      │                               │
      │◄─── event: tool_result ──────│  {"tool": "search_competitors",
      │                               │   "summary": "Found 5",
      │                               │   "count": 5}
      │                               │
      │◄─── event: tool_start ───────│  {"tool": "get_awards"}
      │◄─── event: tool_result ──────│  {"summary": "12 recent awards"}
      │                               │
      │                               │  [LLM synthesizing]
      │◄─── event: text ─────────────│  {"content": "Based on..."}
      │◄─── event: text ─────────────│  {"content": " analysis..."}
      │     ... (streaming) ...        │
      │                               │
      │◄─── event: suggestions ──────│  {"suggestions": ["Deep dive?",
      │                               │   "Compare with SAIC?"]}
      │                               │
      │◄─── event: done ─────────────│  {"tokens_used": 4523,
      │                               │   "tools_called": 2}
      │                               │
      │              [Connection closes]
```

## Infrastructure

```
    GitHub Actions CI/CD
    ┌─────────────────────────────────────────┐
    │ push to main ──►                        │
    │   Build frontend (npm run build)        │
    │   Build 2 Docker images                 │
    │   Scan vulnerabilities (Trivy)          │
    │   Push to ghcr.io                       │
    │   SSH ──► docker compose pull && up -d  │
    └─────────────────────────────────────────┘
              │
              ▼
    AWS Lightsail Instance
    ┌─────────────────────────────────────────────────────┐
    │                                                     │
    │   ┌─────────┐  ┌──────────────────────────────────┐│
    │   │  nginx   │  │         app (FastAPI)            ││
    │   │  (SSL,   │  │  ┌──────────────────────────┐   ││
    │   │  static  │──│  │  gunicorn (2 workers)     │   ││
    │   │  assets) │  │  │  max-requests: 300        │   ││
    │   └─────────┘  │  │  graceful-timeout: 30      │   ││
    │                 │  └──────────────────────────┘   ││
    │                 └──────────────────────────────────┘│
    │                                                     │
    │   ┌──────────────┐  ┌──────────────┐               │
    │   │ celery-default│  │ celery-heavy  │               │
    │   │ concurrency=4 │  │ concurrency=1 │               │
    │   │ (lightweight)  │  │ (ML, bulk AI) │               │
    │   └──────────────┘  └──────────────┘               │
    │                                                     │
    │   ┌──────────────┐                                  │
    │   │ celery-beat   │ ◄── Scheduled: ingestion,       │
    │   │ (scheduler)   │     enrichment, data retention  │
    │   └──────────────┘                                  │
    │                                                     │
    │   ┌──────────────┐  ┌──────────────┐               │
    │   │  PostgreSQL   │  │    Redis      │               │
    │   │  (primary DB) │  │  (cache +     │               │
    │   │              │  │   task queue) │               │
    │   └──────────────┘  └──────────────┘               │
    │                                                     │
    │   Health: /healthz (liveness) /readyz (readiness)   │
    └─────────────────────────────────────────────────────┘
```
