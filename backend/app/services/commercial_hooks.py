"""Compatibility re-export of :mod:`app.services.host_hooks`.

Prefer importing ``host_hooks`` in new code. This module remains so existing
host loaders that still ``from app.services.commercial_hooks import …`` keep
working during the rename window.
"""

from app.services.host_hooks import (  # noqa: F401
    AiQuotaAssert,
    AiUsageRecorder,
    assert_ai_quota,
    assert_platform_quota,
    clear_usage_record_failures,
    enrich_workspace_export,
    get_last_usage_record_failure,
    get_usage_record_failure_count,
    list_background_task_factories,
    list_middleware_factories,
    record_ai_usage,
    record_usage_event,
    register_ai_quota_assert,
    register_ai_usage_recorder,
    register_background_task,
    register_middleware_factory,
    register_platform_quota_assert,
    register_usage_event_recorder,
    register_workspace_export_enricher,
    set_subscription_enforcement,
    subscription_enforcement_enabled,
)

__all__ = [
    "AiQuotaAssert",
    "AiUsageRecorder",
    "assert_ai_quota",
    "assert_platform_quota",
    "clear_usage_record_failures",
    "enrich_workspace_export",
    "get_last_usage_record_failure",
    "get_usage_record_failure_count",
    "list_background_task_factories",
    "list_middleware_factories",
    "record_ai_usage",
    "record_usage_event",
    "register_ai_quota_assert",
    "register_ai_usage_recorder",
    "register_background_task",
    "register_middleware_factory",
    "register_platform_quota_assert",
    "register_usage_event_recorder",
    "register_workspace_export_enricher",
    "set_subscription_enforcement",
    "subscription_enforcement_enabled",
]
