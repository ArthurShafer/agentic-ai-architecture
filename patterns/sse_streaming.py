"""
Typed SSE Streaming Protocol for Agentic AI Systems.

This is a genericized reference implementation of the production pattern.
See docs/sse-streaming.md for full documentation.
"""

import asyncio
import json
import time
from dataclasses import dataclass, asdict
from typing import AsyncIterator


@dataclass
class SSEEvent:
    """A typed Server-Sent Event."""
    event: str
    data: dict

    def serialize(self) -> str:
        """Serialize to SSE wire format."""
        return f"event: {self.event}\ndata: {json.dumps(self.data)}\n\n"


class SSEEmitter:
    """
    Manages SSE event emission with heartbeat support.

    Handles:
    - Typed event emission (text, tool_start, tool_result, etc.)
    - Heartbeat keep-alive during tool execution gaps
    - Connection state tracking
    """

    def __init__(self, queue: asyncio.Queue):
        self._queue = queue
        self._last_event_time = time.time()
        self._heartbeat_task: asyncio.Task | None = None
        self._closed = False

    async def emit(self, event: SSEEvent):
        """Emit a typed SSE event."""
        if self._closed:
            return
        await self._queue.put(event.serialize())
        self._last_event_time = time.time()

    async def emit_heartbeat(self):
        """
        Emit a heartbeat as an SSE comment.

        SSE comments (lines starting with ':') are invisible to application-level
        event handlers but reset proxy/CDN timeout timers.
        """
        if not self._closed:
            await self._queue.put(": heartbeat\n\n")
            self._last_event_time = time.time()

    def start_heartbeat(self, interval_seconds: float = 15.0):
        """Start background heartbeat task."""
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(interval_seconds)
        )

    def stop_heartbeat(self):
        """Stop background heartbeat task."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def _heartbeat_loop(self, interval: float):
        """Send heartbeats when no events have been emitted recently."""
        try:
            while not self._closed:
                await asyncio.sleep(interval)
                if time.time() - self._last_event_time >= interval:
                    await self.emit_heartbeat()
        except asyncio.CancelledError:
            pass

    async def close(self):
        """Close the emitter and stop heartbeats."""
        self._closed = True
        self.stop_heartbeat()
        await self._queue.put(None)  # Sentinel for stream end


async def sse_response_generator(queue: asyncio.Queue) -> AsyncIterator[str]:
    """
    FastAPI-compatible async generator that yields SSE events from a queue.

    Usage with FastAPI:
        from starlette.responses import StreamingResponse

        @app.post("/chat/stream")
        async def stream_chat(request: ChatRequest):
            queue = asyncio.Queue()
            emitter = SSEEmitter(queue)

            # Start agent loop in background
            asyncio.create_task(run_agentic_loop(messages, config, emitter))

            return StreamingResponse(
                sse_response_generator(queue),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",  # Disable nginx buffering
                },
            )
    """
    while True:
        event = await queue.get()
        if event is None:  # Stream end sentinel
            break
        yield event


# --- Convenience functions for common events ---

def text_event(content: str) -> SSEEvent:
    return SSEEvent("text", {"content": content})

def tool_start_event(tool: str, description: str) -> SSEEvent:
    return SSEEvent("tool_start", {"tool": tool, "description": description})

def tool_result_event(tool: str, summary: str, count: int) -> SSEEvent:
    return SSEEvent("tool_result", {"tool": tool, "summary": summary, "count": count})

def done_event(tokens_used: int, tools_called: int) -> SSEEvent:
    return SSEEvent("done", {"tokens_used": tokens_used, "tools_called": tools_called})

def error_event(message: str) -> SSEEvent:
    return SSEEvent("error", {"message": message})

def session_event(session_id: str) -> SSEEvent:
    return SSEEvent("session", {"session_id": session_id})

def widget_event(widget_id: str, title: str, position: int) -> SSEEvent:
    return SSEEvent("widget_created", {
        "widget_id": widget_id, "title": title, "position": position,
    })

def nudge_event(message: str, target: str, query: str = "") -> SSEEvent:
    return SSEEvent("nudge", {"message": message, "target": target, "query": query})

def suggestions_event(suggestions: list[str]) -> SSEEvent:
    return SSEEvent("suggestions_event", {"suggestions": suggestions})
