"""Deliver transient model retry status through the existing turn event stream."""

import asyncio
from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any

_status_sink: ContextVar[asyncio.Queue | None] = ContextVar(
    "model_retry_status", default=None
)


async def emit_model_retry_status(attempt: int, maximum: int) -> None:
    """Show the next physical attempt without exposing provider errors."""
    sink = _status_sink.get()
    if sink is not None:
        await sink.put(
            (
                "event",
                {
                    "type": "status",
                    "text": (
                        f"Retrying · {attempt} of {maximum}" if attempt else "Thinking"
                    ),
                },
                None,
            )
        )


async def stream_with_retry_status(
    source: AsyncIterator[dict[str, Any]]
) -> AsyncIterator[dict[str, Any]]:
    """Keep one producer task for the whole turn; bound buffering and cancellation."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=32)

    async def produce() -> None:
        reset = _status_sink.set(queue)
        try:
            async for event in source:
                acknowledged = asyncio.get_running_loop().create_future()
                await queue.put(("event", event, acknowledged))
                # Do not cross a persistence boundary until the consumer asks
                # for the next event (notably the initial session metadata).
                await acknowledged
        finally:
            _status_sink.reset(reset)
            await source.aclose()

    task = asyncio.create_task(produce())
    try:
        while True:
            if task.done() and queue.empty():
                await task
                return
            pending = asyncio.create_task(queue.get())
            try:
                done, _ = await asyncio.wait(
                    {pending, task}, return_when=asyncio.FIRST_COMPLETED
                )
                if pending in done:
                    _, value, acknowledged = pending.result()
                    try:
                        yield value
                    finally:
                        if acknowledged is not None and not acknowledged.done():
                            acknowledged.set_result(None)
            finally:
                pending.cancel()
                try:
                    await pending
                except asyncio.CancelledError:
                    pass
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
