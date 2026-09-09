"""
This is the server-sent events layer. The barista view is the reason the app
exists at all and it is watched on an iPad on cafe wifi, so two rules hold here
and both had to hold before this went behind a proxy. Nothing is broadcast
until it is committed, because the stream announces facts rather than
intentions. And a reconnecting client resyncs whole state through GET /queue
before it resumes the stream from Last-Event-ID, because a resumed stream alone
would leave a client that missed events showing a queue that is quietly wrong,
which is worse than one that is obviously stale. Imported by the routes, which
publish through the broadcaster.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from typing import AsyncIterator, Iterable, Iterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.db import events_since, session_scope
from core.events import Event

__all__ = ["Broadcaster", "broadcaster", "router"]

QUEUE_LIMIT = 1000  # a slow client is dropped rather than allowed to grow


class Broadcaster:
    """Fan-out of committed events to every open stream."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def publish(self, events: Iterable[Event]) -> int:
        """Called after commit. Never awaits, never blocks a request."""
        sent = 0
        for event in events:
            for queue in list(self._subscribers):
                try:
                    queue.put_nowait(event)
                    sent += 1
                except asyncio.QueueFull:
                    self._subscribers.discard(queue)
        return sent

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


broadcaster = Broadcaster()

router = APIRouter(tags=["stream"])


def _sse(event: Event) -> dict[str, str]:
    return {
        "id": event.event_id,
        "event": str(event.type),
        "data": json.dumps(event.row(), default=str),
    }


def _seq_from(last_event_id: str | None) -> int | None:
    """Event ids look like `live:0000042`; the tail is the resume point."""
    if not last_event_id or ":" not in last_event_id:
        return None
    try:
        return int(last_event_id.rsplit(":", 1)[1])
    except ValueError:
        return None


@router.get("/stream")
async def stream(request: Request) -> EventSourceResponse:
    """Live event stream. Resumes from `Last-Event-ID` when the client sends it."""
    resume_from = _seq_from(
        request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    )

    async def publisher() -> AsyncIterator[dict[str, str]]:
        with broadcaster.subscribe() as queue:
            if resume_from is not None:
                with session_scope() as session:
                    for missed in events_since(session, resume_from):
                        yield _sse(missed)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=settings.heartbeat_s)
                except asyncio.TimeoutError:
                    continue          # sse-starlette's ping keeps the socket warm
                yield _sse(event)

    return EventSourceResponse(
        publisher(),
        ping=int(settings.heartbeat_s),
        headers={
            # a buffering proxy makes SSE look fine locally and fail in production
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
        },
    )
