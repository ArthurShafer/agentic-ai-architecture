# SSE Streaming Protocol

A typed Server-Sent Events protocol for real-time AI agent communication — streaming text, tool execution visibility, widget creation, and navigation events.

## The Problem

AI agents that run tools take 10-60 seconds to complete. Without streaming:
- Users see a blank screen and wonder if the system is hung
- Proxy servers and CDNs may timeout (typically 30-60s)
- There's no way to show *what* the agent is doing (which tools it's calling)
- The "all or nothing" response pattern wastes the latency budget

## The Protocol

### Event Types

| Event | Payload | Purpose |
|-------|---------|---------|
| `text` | `{content: string}` | Streaming text chunks from the LLM |
| `tool_start` | `{tool: string, description: string}` | Tool execution began |
| `tool_result` | `{tool: string, summary: string, count: int}` | Tool finished with results |
| `widget_created` | `{widget_id: string, title: string, position: int}` | Dynamic widget/card created |
| `session` | `{session_id: string}` | Session established (first event) |
| `done` | `{tokens_used: int, tools_called: int}` | Turn complete |
| `error` | `{message: string}` | Error occurred |
| `navigation_event` | `{target: string, context: dict}` | Frontend navigation trigger |
| `suggestions_event` | `{suggestions: list[str]}` | Follow-up suggestion chips |
| `nudge` | `{message: string, query: string}` | Suggest deeper analysis surface |

### Wire Format

```
event: session
data: {"session_id": "abc-123"}

event: text
data: {"content": "Let me analyze "}

event: text
data: {"content": "the competitive landscape."}

event: tool_start
data: {"tool": "search_competitors", "description": "Finding competitors for NAICS 541512"}

event: tool_result
data: {"tool": "search_competitors", "summary": "Found 5 competitors with recent awards", "count": 5}

event: text
data: {"content": "Based on the competitive analysis..."}

event: done
data: {"tokens_used": 4523, "tools_called": 3}
```

### Heartbeat Pattern

Use SSE comment format for keep-alive pings:

```
: heartbeat
```

SSE comments (lines starting with `:`) are **invisible to application-level event handlers** — they won't trigger `onmessage` or named event listeners. But they **do reset proxy/CDN timeout timers**, preventing connection drops during long tool executions.

Send heartbeats every 15 seconds during tool execution gaps.

### Frontend Integration

```javascript
const eventSource = new EventSource('/api/chat/stream');

eventSource.addEventListener('text', (e) => {
  const { content } = JSON.parse(e.data);
  appendToChat(content);  // Progressive rendering
});

eventSource.addEventListener('tool_start', (e) => {
  const { tool, description } = JSON.parse(e.data);
  showToolIndicator(tool, description);  // "Searching competitors..."
});

eventSource.addEventListener('tool_result', (e) => {
  const { tool, summary, count } = JSON.parse(e.data);
  updateToolIndicator(tool, `${summary} (${count} results)`);
});

eventSource.addEventListener('done', (e) => {
  const { tokens_used, tools_called } = JSON.parse(e.data);
  finalizeChat(tokens_used, tools_called);
});
```

### Error Recovery

The `done` event serves as the authoritative "turn complete" signal. If the SSE connection drops:

1. Frontend reconnects with `Last-Event-Id` header
2. Backend checks session state and either resumes streaming or sends a `done` event
3. If reconnection fails 3 times, frontend falls back to polling the session endpoint

### Design Decision: Why SSE Over WebSockets?

- **Simpler infrastructure** — SSE works over standard HTTP, no upgrade handshake
- **Built-in reconnection** — browsers handle reconnection automatically
- **One-directional fits the pattern** — AI streaming is server→client; user input goes via POST
- **Proxy-friendly** — SSE passes through most corporate proxies without configuration
- **Cost clarity** — each SSE connection maps to one "turn," making token/cost tracking straightforward

WebSockets would only be justified if bidirectional streaming (e.g., voice input + text output simultaneously) were required.
