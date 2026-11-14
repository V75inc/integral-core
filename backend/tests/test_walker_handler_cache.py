"""Cached Walker handler registration must match the original behaviour."""

import inspect

from jvspatial.core.entities.walker import Walker

from app.services import jvspatial_walker_fast as fast


def _original(self):
    for _name, method in inspect.getmembers(self.__class__, inspect.isfunction):
        if hasattr(method, "_is_event_handler"):
            for event_type in getattr(method, "_event_types", []):
                if event_type not in self._event_handlers:
                    self._event_handlers[event_type] = []
                self._event_handlers[event_type].append(method)


class _W(Walker):
    pass


def _handler(*types):
    def fn(self, *a, **k):
        return None

    fn._is_event_handler = True
    fn._event_types = list(types)
    return fn


class _WithHandlers(Walker):
    on_a = _handler("a", "b")
    on_b = _handler("b")


def _handlers(cls, registrar):
    w = cls.__new__(cls)
    w._event_handlers = {}
    registrar(w)
    return {k: [m.__name__ for m in v] for k, v in w._event_handlers.items()}


def test_cached_registration_matches_original_for_plain_and_decorated_walkers():
    fast.reset_walker_handler_cache()
    for cls in (Walker, _W, _WithHandlers):
        assert _handlers(cls, fast._register_event_handlers_cached) == _handlers(
            cls, _original
        )
        # second construction uses the cache and still matches
        assert _handlers(cls, fast._register_event_handlers_cached) == _handlers(
            cls, _original
        )


def test_instances_do_not_share_handler_lists():
    fast.reset_walker_handler_cache()
    a = _WithHandlers.__new__(_WithHandlers)
    a._event_handlers = {}
    b = _WithHandlers.__new__(_WithHandlers)
    b._event_handlers = {}
    fast._register_event_handlers_cached(a)
    fast._register_event_handlers_cached(b)
    a._event_handlers["a"].append("x")
    assert "x" not in b._event_handlers["a"]


def test_install_is_idempotent():
    fast._installed = False
    assert fast.install() in (True, False)
    assert fast.install() is False


import asyncio


class _Exits(Walker):
    calls: list = []

    async def b_exit(self):
        self.calls.append("b")

    async def a_exit(self):
        self.calls.append("a")

    async def boom(self):
        raise RuntimeError("x")

    b_exit._on_exit = True
    a_exit._on_exit = True
    boom._on_exit = True


async def _original_exit(self):
    for _name, method in inspect.getmembers(self.__class__, inspect.isfunction):
        if hasattr(method, "_on_exit") and method._on_exit:
            try:
                if asyncio.iscoroutinefunction(method):
                    await method(self)
                else:
                    method(self)
            except Exception as e:
                await self.report(f"Error in exit hook: {e}")


def _run_exit(fn):
    w = _Exits.__new__(_Exits)
    w._report = []
    _Exits.calls = []

    async def go():
        await fn(w)

    asyncio.run(go())
    return list(_Exits.calls), list(w._report)


def test_exit_hooks_match_original_order_and_error_reporting():
    fast.reset_walker_handler_cache()
    assert _run_exit(fast._execute_exit_hooks_cached) == _run_exit(_original_exit)
    assert _run_exit(fast._execute_exit_hooks_cached) == _run_exit(_original_exit)
    calls, report = _run_exit(fast._execute_exit_hooks_cached)
    assert calls == ["a", "b"] and report == ["Error in exit hook: x"]
