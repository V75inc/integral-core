"""Per-class caches for jvspatial ``Walker`` handler discovery.

Covers ``_register_event_handlers`` (``@on_emit``, run in ``__init__``) and
``_execute_exit_hooks`` (``@on_exit``, run at the end of every traversal); both used to
call ``inspect.getmembers`` on the walker class each time.

``Walker.__init__`` calls ``_register_event_handlers``, which runs
``inspect.getmembers`` over the whole walker class and re-tests every member for the
``@on_emit`` marker, on every instance. Core builds a Walker for each access check
(``ResourceLocationWalker`` and friends), so a Tracks list or an App page constructs
hundreds of them and about 18% of the request CPU went to repeating that reflection.

The set of handlers is a property of the class, not of the instance, so it is computed
once per class and applied to each new instance exactly as the original did (same
event types, same order, appended to that instance's own ``_event_handlers`` lists).
A class whose handlers are changed after its first instance (not done anywhere in
Core) can call ``reset_walker_handler_cache``.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Dict, List, Tuple

_Pairs = Tuple[Tuple[Any, Any], ...]
_CLASS_HANDLERS: Dict[type, _Pairs] = {}
_CLASS_EXIT_HOOKS: Dict[type, Tuple[Any, ...]] = {}
_installed = False


def _handlers_for(cls: type) -> _Pairs:
    pairs = _CLASS_HANDLERS.get(cls)
    if pairs is None:
        found: List[Tuple[Any, Any]] = []
        for _name, method in inspect.getmembers(cls, inspect.isfunction):
            if hasattr(method, "_is_event_handler"):
                for event_type in getattr(method, "_event_types", []):
                    found.append((event_type, method))
        pairs = tuple(found)
        _CLASS_HANDLERS[cls] = pairs
    return pairs


def _register_event_handlers_cached(self: Any) -> None:
    for event_type, method in _handlers_for(self.__class__):
        if event_type not in self._event_handlers:
            self._event_handlers[event_type] = []
        self._event_handlers[event_type].append(method)


def _exit_hooks_for(cls: type) -> Tuple[Any, ...]:
    hooks = _CLASS_EXIT_HOOKS.get(cls)
    if hooks is None:
        hooks = tuple(
            method
            for _name, method in inspect.getmembers(cls, inspect.isfunction)
            if hasattr(method, "_on_exit") and method._on_exit
        )
        _CLASS_EXIT_HOOKS[cls] = hooks
    return hooks


async def _execute_exit_hooks_cached(self: Any) -> None:
    """Same behaviour as ``Walker._execute_exit_hooks`` without re-reflecting the class."""
    for method in _exit_hooks_for(self.__class__):
        try:
            if asyncio.iscoroutinefunction(method):
                await method(self)
            else:
                method(self)
        except Exception as e:
            await self.report(f"Error in exit hook: {e}")


def reset_walker_handler_cache() -> None:
    _CLASS_HANDLERS.clear()
    _CLASS_EXIT_HOOKS.clear()


def install() -> bool:
    """Replace ``Walker._register_event_handlers`` with the cached version (idempotent)."""
    global _installed
    if _installed:
        return False
    try:
        from jvspatial.core.entities.walker import Walker
    except Exception:  # pragma: no cover - library layout changed; keep the original
        return False
    Walker._register_event_handlers = _register_event_handlers_cached  # type: ignore[method-assign]
    Walker._execute_exit_hooks = _execute_exit_hooks_cached  # type: ignore[method-assign]
    _installed = True
    return True
