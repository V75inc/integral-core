"""Optional host-extension hooks. Open-source Core leaves every slot empty.

A host process (e.g. Integral Business) may register implementations after
boot via ``INTEGRAL_HOST_EXTENSION_MODULE`` (alias: ``INTEGRAL_BILLING_MODULE``).
Core call sites invoke these helpers and treat missing handlers as no-ops.

Authorization (pre-operation deny) is separate from metering (post-operation
usage signal). Metering failures must stay observable — they are logged and
recorded on the module, not silently discarded.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# workspace_id → raise or pass (authorization)
PlatformQuotaAssert = Callable[[str], Awaitable[Any]]
# kwargs for a host usage/event recorder (metering)
UsageEventRecorder = Callable[..., Awaitable[Any]]
# (data, workspace_id) → mutated/extended dict (opaque host fields only)
WorkspaceExportEnricher = Callable[[Dict[str, Any], str], Awaitable[Dict[str, Any]]]
# zero-arg coroutine factory started at boot
BackgroundTaskFactory = Callable[[], Awaitable[Any]]
# Starlette middleware class (or None)
MiddlewareFactory = Callable[[], Any]

_platform_quota_assert: Optional[PlatformQuotaAssert] = None
_usage_event_recorder: Optional[UsageEventRecorder] = None
_workspace_enricher: Optional[WorkspaceExportEnricher] = None
_background_task_factories: List[BackgroundTaskFactory] = []
_middleware_factories: List[MiddlewareFactory] = []
_subscription_enforcement: bool = False

_lock = threading.Lock()
_last_usage_record_failure: Optional[Dict[str, Any]] = None
_usage_record_failure_count: int = 0

# Back-compat aliases for hosts still registering under the older names.
AiQuotaAssert = PlatformQuotaAssert
AiUsageRecorder = UsageEventRecorder


def register_ai_quota_assert(fn: Optional[PlatformQuotaAssert]) -> None:
    """Register (or clear) the pre-operation platform-key quota gate."""
    global _platform_quota_assert
    _platform_quota_assert = fn


def register_ai_usage_recorder(fn: Optional[UsageEventRecorder]) -> None:
    """Register (or clear) the post-operation usage/event recorder."""
    global _usage_event_recorder
    _usage_event_recorder = fn


def register_workspace_export_enricher(fn: Optional[WorkspaceExportEnricher]) -> None:
    """Register (or clear) a workspace list/detail payload enricher."""
    global _workspace_enricher
    _workspace_enricher = fn


def register_background_task(factory: BackgroundTaskFactory) -> None:
    """Append a coroutine factory started once during API boot."""
    if factory not in _background_task_factories:
        _background_task_factories.append(factory)


def register_middleware_factory(factory: MiddlewareFactory) -> None:
    """Append a Starlette middleware class factory for API boot."""
    if factory not in _middleware_factories:
        _middleware_factories.append(factory)


def set_subscription_enforcement(enabled: bool) -> None:
    """When True, workspace admins cannot manually grant/revoke entitlements."""
    global _subscription_enforcement
    _subscription_enforcement = bool(enabled)


def subscription_enforcement_enabled() -> bool:
    """Return whether host subscription enforcement is active."""
    return _subscription_enforcement


def list_background_task_factories() -> List[BackgroundTaskFactory]:
    """Return registered background-task factories (copy)."""
    return list(_background_task_factories)


def list_middleware_factories() -> List[MiddlewareFactory]:
    """Return registered middleware factories (copy)."""
    return list(_middleware_factories)


def get_last_usage_record_failure() -> Optional[Dict[str, Any]]:
    """Return the most recent metering failure snapshot, if any."""
    with _lock:
        return dict(_last_usage_record_failure) if _last_usage_record_failure else None


def get_usage_record_failure_count() -> int:
    """Return how many metering recorder failures have been observed."""
    with _lock:
        return _usage_record_failure_count


def clear_usage_record_failures() -> None:
    """Reset metering failure observability state (tests / recovery tools)."""
    global _last_usage_record_failure, _usage_record_failure_count
    with _lock:
        _last_usage_record_failure = None
        _usage_record_failure_count = 0


async def assert_ai_quota(workspace_id: str) -> None:
    """Authorization: no-op unless a host registered a quota gate."""
    if _platform_quota_assert is None:
        return
    await _platform_quota_assert(workspace_id)


async def record_ai_usage(**kwargs: Any) -> Any:
    """Metering: no-op unless a host registered a usage recorder.

    Failures are logged and retained via ``get_last_usage_record_failure`` so
    they are observable/recoverable. The caller's request path is not aborted
    (authorization already ran separately via ``assert_ai_quota``).
    """
    if _usage_event_recorder is None:
        return None
    try:
        return await _usage_event_recorder(**kwargs)
    except Exception as exc:  # noqa: BLE001
        global _last_usage_record_failure, _usage_record_failure_count
        workspace_id = kwargs.get("workspace_id")
        logger.exception(
            "host usage recorder failed workspace_id=%s",
            workspace_id,
        )
        with _lock:
            _usage_record_failure_count += 1
            _last_usage_record_failure = {
                "workspace_id": workspace_id,
                "error": f"{type(exc).__name__}: {exc}",
                "kwargs_keys": sorted(str(k) for k in kwargs.keys()),
            }
        return {
            "ok": False,
            "error": "usage_record_failed",
            "workspace_id": workspace_id,
        }


async def enrich_workspace_export(
    data: Dict[str, Any], workspace_id: str
) -> Dict[str, Any]:
    """Pass-through unless a host registered an opaque-field enricher."""
    if _workspace_enricher is None:
        return data
    try:
        enriched = await _workspace_enricher(data, workspace_id)
        return enriched if isinstance(enriched, dict) else data
    except Exception:  # noqa: BLE001
        logger.exception("host workspace enricher failed ws=%s", workspace_id)
        return data
