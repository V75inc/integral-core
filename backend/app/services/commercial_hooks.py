"""Optional commercial-cell hooks. Open-source Core leaves every slot empty.

Business registers implementations when ``INTEGRAL_BILLING_MODULE`` loads.
Core call sites invoke these helpers and treat missing handlers as no-ops.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# workspace_id → raise or pass
AiQuotaAssert = Callable[[str], Awaitable[Any]]
# kwargs matching record_ai_usage signature
AiUsageRecorder = Callable[..., Awaitable[Any]]
# (data, workspace_id) → mutated/extended dict
WorkspaceExportEnricher = Callable[[Dict[str, Any], str], Awaitable[Dict[str, Any]]]
# zero-arg coroutine factory started at boot
BackgroundTaskFactory = Callable[[], Awaitable[Any]]
# Starlette middleware class (or None)
MiddlewareFactory = Callable[[], Any]

_ai_quota_assert: Optional[AiQuotaAssert] = None
_ai_usage_recorder: Optional[AiUsageRecorder] = None
_workspace_enricher: Optional[WorkspaceExportEnricher] = None
_background_task_factories: List[BackgroundTaskFactory] = []
_middleware_factories: List[MiddlewareFactory] = []
_subscription_enforcement: bool = False


def register_ai_quota_assert(fn: Optional[AiQuotaAssert]) -> None:
    global _ai_quota_assert
    _ai_quota_assert = fn


def register_ai_usage_recorder(fn: Optional[AiUsageRecorder]) -> None:
    global _ai_usage_recorder
    _ai_usage_recorder = fn


def register_workspace_export_enricher(fn: Optional[WorkspaceExportEnricher]) -> None:
    global _workspace_enricher
    _workspace_enricher = fn


def register_background_task(factory: BackgroundTaskFactory) -> None:
    """Append a coroutine factory started once during API boot."""
    if factory not in _background_task_factories:
        _background_task_factories.append(factory)


def register_middleware_factory(factory: MiddlewareFactory) -> None:
    if factory not in _middleware_factories:
        _middleware_factories.append(factory)


def set_subscription_enforcement(enabled: bool) -> None:
    """When True, workspace admins cannot manually grant/revoke entitlements."""
    global _subscription_enforcement
    _subscription_enforcement = bool(enabled)


def subscription_enforcement_enabled() -> bool:
    return _subscription_enforcement


def list_background_task_factories() -> List[BackgroundTaskFactory]:
    return list(_background_task_factories)


def list_middleware_factories() -> List[MiddlewareFactory]:
    return list(_middleware_factories)


async def assert_ai_quota(workspace_id: str) -> None:
    """No-op unless Business registered a quota gate."""
    if _ai_quota_assert is None:
        return
    await _ai_quota_assert(workspace_id)


async def record_ai_usage(**kwargs: Any) -> Any:
    """No-op (returns None) unless Business registered a recorder."""
    if _ai_usage_recorder is None:
        return None
    try:
        return await _ai_usage_recorder(**kwargs)
    except Exception:  # noqa: BLE001
        logger.exception("commercial ai usage recorder failed")
        return None


async def enrich_workspace_export(
    data: Dict[str, Any], workspace_id: str
) -> Dict[str, Any]:
    """Pass-through unless Business registered a plan/usage enricher."""
    if _workspace_enricher is None:
        return data
    try:
        enriched = await _workspace_enricher(data, workspace_id)
        return enriched if isinstance(enriched, dict) else data
    except Exception:  # noqa: BLE001
        logger.exception("commercial workspace enricher failed ws=%s", workspace_id)
        return data
