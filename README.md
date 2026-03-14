# Agentic AI Architecture

Production architecture patterns for building **agentic AI systems** — multi-tool agent loops with streaming, retrieval-augmented generation, predictive intelligence, and real-time executive briefing.

This repository documents the architecture and design patterns behind a production intelligence platform that processes federal contracting data using autonomous AI agents. The system is deployed on AWS and serves live users daily.

> **Note:** This is an architecture showcase. The production codebase is private. Code samples here are genericized reference implementations of the patterns used in production.

---

## System Overview

The platform combines multiple AI subsystems into a unified intelligence pipeline:

```
┌─────────────────────────────────────────────────────────────────┐
│                     Frontend (React SPA)                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ Dashboard │  │ Pipeline │  │ Briefing │  │ War Room Chat │  │
│  └──────────┘  └──────────┘  └──────────┘  └───────────────┘  │
└────────────────────────┬────────────────────────────────────────┘
                         │ SSE / REST
┌────────────────────────▼────────────────────────────────────────┐
│                   FastAPI Backend                                │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ War Room    │  │ Intelligence │  │ Sentinel               │ │
│  │ (27 tools,  │  │ Brief Engine │  │ (6 tools, lightweight, │ │
│  │  streaming, │  │ (wave-based  │  │  request/response)     │ │
│  │  agentic)   │  │  parallel)   │  │                        │ │
│  └──────┬──────┘  └──────┬───────┘  └────────┬───────────────┘ │
│         │                │                    │                  │
│  ┌──────▼────────────────▼────────────────────▼───────────────┐ │
│  │              Shared Tool Dispatch Layer                     │ │
│  │  Registry → Dispatch Table → Handler → Sanitize → Cache    │ │
│  └──────┬─────────────────────────────────────────────────────┘ │
│         │                                                       │
│  ┌──────▼──────┐  ┌──────────┐  ┌────────────┐  ┌──────────┐  │
│  │ LLM Provider│  │ Scoring  │  │ Prediction │  │ Document │  │
│  │ Abstraction │  │ Engine   │  │ Engine     │  │ Analysis │  │
│  └─────────────┘  └──────────┘  └────────────┘  └──────────┘  │
└────────────────────────┬────────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
   PostgreSQL         Redis          Vector DB
   (primary)        (cache/queue)    (embeddings)
```

---

## Architecture Patterns

### 1. [Agentic Loop with Budget Awareness](docs/agentic-loop.md)
The core agent loop that drives autonomous tool use — including budget injection, diminishing returns detection, circuit breaking, and forced synthesis. Inspired by research on token-aware agent steering (arXiv:2511.17006).

### 2. [Tool Registry & Dispatch](docs/tool-dispatch.md)
Schema/handler separation with deferred imports, fresh DB session isolation per tool call, CRAG (Corrective RAG) for retrieval tools, and output sanitization pipeline.

### 3. [Multi-Surface Agent Configuration](docs/surface-config.md)
One engine, multiple UI surfaces — each with different model tiers, tool budgets, and depth limits. Graceful degradation with escalation nudges.

### 4. [SSE Streaming Protocol](docs/sse-streaming.md)
Typed Server-Sent Events protocol for real-time AI streaming with tool execution visibility, widget creation, navigation events, and heartbeat keep-alive.

### 5. [LLM Provider Abstraction](docs/llm-abstraction.md)
Strategy pattern with singleton registry — swap LLM providers without touching agent logic. Tier-based model selection ("fast"/"standard"/"advanced") resolved at runtime.

### 6. [Wave-Based Parallel Generation](docs/wave-parallel.md)
Queue-multiplexed parallel section generation with dependency ordering — used for multi-section intelligence reports where sections build on each other.

### 7. [Observability & After-Action Review](docs/observability.md)
Per-turn trace collection with cost estimation (accounting for prompt caching pricing), anomaly detection, and background-thread diagnostic review.

---

## Reference Implementations

Genericized code samples demonstrating each pattern:

| Pattern | File | Description |
|---------|------|-------------|
| Agentic Loop | [`patterns/agentic_loop.py`](patterns/agentic_loop.py) | Core loop with budget awareness and circuit breaking |
| Tool Dispatch | [`patterns/tool_dispatch.py`](patterns/tool_dispatch.py) | Registry, dispatch table, and handler pattern |
| SSE Streaming | [`patterns/sse_streaming.py`](patterns/sse_streaming.py) | Typed event protocol with async generators |
| LLM Abstraction | [`patterns/llm_provider.py`](patterns/llm_provider.py) | Provider-agnostic streaming with finalizer pattern |
| Trace Collector | [`patterns/trace_collector.py`](patterns/trace_collector.py) | Observability with cost estimation |

---

## Design Decisions

Every pattern in this repo exists because something broke, cost too much, or confused users in production.

**[Read the full Design Decisions doc →](DESIGN_DECISIONS.md)** — 10 decisions with the failure that motivated each one, the fix, and the lesson learned.

Highlights:

| Decision | What Broke Without It |
|----------|----------------------|
| **Fresh DB session per tool call** | Tool A's failed query corrupted Tool B's results via shared ORM session |
| **Budget awareness injection** | Agent burned $2-3 on simple questions; stopped too early on complex ones |
| **USE WHEN / DO NOT USE annotations** | 20% tool mis-selection rate dropped to 8% |
| **Data gap disclosure** | 35% hallucination rate in zero-result areas dropped to <3% |
| **Composite tools** | Common deep-dives cost $0.75 in overhead; now $0.15 |
| **Fire-and-forget AAR** | Slow diagnostics blocked user responses; crashes returned 500 errors |
| **Context compaction** | 20-round conversations hit $1.20/turn in input tokens |

---

## Architecture Diagrams

Visual walkthroughs of each subsystem: **[diagrams/system-architecture.md](diagrams/system-architecture.md)**

- Full system overview (frontend → backend → data stores)
- Agentic loop flow with all safety mechanisms
- Tool dispatch pipeline (cache → session → execute → sanitize → CRAG)
- Wave-based parallel generation
- SSE event timeline (client ↔ server)
- Infrastructure layout (7 Docker containers)

---

## Production Stats

- **27 tools** across the full agent surface
- **7 containers** (app, nginx, postgres, redis, celery-default, celery-heavy, celery-beat)
- **4 agentic surfaces** with different depth/cost profiles
- **6 ML models** for predictive intelligence (XGBoost + Bayesian calibration)
- Deployed on **AWS** with Docker, CI/CD via GitHub Actions, Trivy security scanning

---

## Tech Stack

![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-61DAFB?style=flat-square&logo=react&logoColor=black)
![Claude API](https://img.shields.io/badge/Claude_API-D4A574?style=flat-square&logo=anthropic&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-DC382D?style=flat-square&logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white)
![AWS](https://img.shields.io/badge/AWS-232F3E?style=flat-square&logo=amazonwebservices&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-37814A?style=flat-square&logo=celery&logoColor=white)
![XGBoost](https://img.shields.io/badge/XGBoost-EC4E20?style=flat-square)

---

## Author

**Arthur Shafer** — [arthurshafer.com](https://arthurshafer.com) · [GitHub](https://github.com/ArthurShafer)
