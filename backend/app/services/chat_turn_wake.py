"""Content-free local wakeups. Durable admission and event replay own all data."""

import asyncio
from collections import defaultdict
from weakref import WeakValueDictionary

# One shared edge-triggered notification per work item and event loop.
# No turns or scopes are retained once their readers disappear.
_event_waiters: WeakValueDictionary[
    tuple[asyncio.AbstractEventLoop, str], asyncio.Event
] = WeakValueDictionary()
_idle_workers: dict[asyncio.AbstractEventLoop, set[asyncio.Future[None]]] = defaultdict(
    set
)


def next_chat_event(work_item_id: str) -> asyncio.Event:
    """Subscribe before reading the current durable cursor."""
    key = (asyncio.get_running_loop(), work_item_id)
    signal = _event_waiters.get(key)
    if signal is None:
        signal = asyncio.Event()
        _event_waiters[key] = signal
    return signal


def wake_event_readers(work_item_id: str) -> None:
    """Wake current readers of this work item after its transaction exits."""
    signal = _event_waiters.pop((asyncio.get_running_loop(), work_item_id), None)
    if signal is not None:
        signal.set()


async def wait_for_chat_work(stop: asyncio.Event, timeout: float) -> None:
    """Wait for a local submission, shutdown, or the cross-process polling deadline."""
    loop = asyncio.get_running_loop()
    signal: asyncio.Future[None] = loop.create_future()
    waiters = _idle_workers[loop]
    waiters.add(signal)
    stopped = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait(
            (signal, stopped), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        waiters.discard(signal)
        if not waiters:
            _idle_workers.pop(loop, None)
        signal.cancel()
        stopped.cancel()
        await asyncio.gather(stopped, return_exceptions=True)


def wake_chat_worker() -> None:
    """Wake one idle worker to attempt a normal durable claim."""
    for signal in _idle_workers.get(asyncio.get_running_loop(), ()):
        if not signal.done():
            signal.set_result(None)
            break
