"""Optional host-extension hooks. Open-source Core leaves every slot empty.

A host process (e.g. Integral Business) may register implementations after
boot via ``INTEGRAL_HOST_EXTENSION_MODULE``. Core call sites invoke these
helpers. Most missing handlers are no-ops; bounded-work price admission instead
refuses an absent resolver through the work price service.

Authorization (pre-operation deny) is separate from metering (post-operation
usage signal). Metering failures must stay observable — they are logged and
recorded on the module, not silently discarded.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterator,
    List,
    Literal,
    Optional,
    Sequence,
)

from app.schemas.agentive.model_dispatch import ModelDispatchInput, ModelPayloadBounds
from app.schemas.agentive.work_price import ModelPriceRequest, ModelTokenPriceEvidence

logger = logging.getLogger(__name__)

# A host upgrading to this interface must qualify any persisted compatibility
# state before serving. The migration and verification of its own data are host-owned.
APP_BOUNDARY_CUTOVER_REQUIRED = True

# workspace_id → raise or pass (authorization)
PlatformQuotaAssert = Callable[[str], Awaitable[Any]]
# kwargs for a host usage/event recorder (metering)
UsageEventRecorder = Callable[..., Awaitable[Any]]
# (data, workspace_id) → mutated/extended dict (opaque host fields only)
WorkspaceExportEnricher = Callable[[Dict[str, Any], str], Awaitable[Dict[str, Any]]]
# zero-arg coroutine factory started at boot
BackgroundTaskFactory = Callable[[], Awaitable[Any]]
# Required, read-only compatibility assertions before the host serves traffic.
StartupReadinessCheck = Callable[[], Awaitable[object]]
HostToolHandler = Callable[[Dict[str, Any], Any], Awaitable[Dict[str, Any]]]
SignatureVaultOperation = Literal["list", "save", "delete"]
SignatureVaultResult = Dict[str, Any] | List[Dict[str, Any]] | bool | None
SignatureVaultProvider = Callable[
    [SignatureVaultOperation, str, str, Dict[str, Any]], Awaitable[SignatureVaultResult]
]
# Starlette middleware class (or None)
MiddlewareFactory = Callable[[], Any]
# (request, action, workspace_id) → raise or pass
EntitlementMutationAuthorizer = Callable[[Any, str, str], Awaitable[Any]]
# Trusted host access override; absence retains durable entitlement enforcement.
EntitlementAccessResolver = Callable[[str, str, str], Awaitable[bool]]
ModelPriceResolver = Callable[[ModelPriceRequest], Awaitable[ModelTokenPriceEvidence]]
ModelBoundsResolver = Callable[[ModelDispatchInput], Awaitable[ModelPayloadBounds]]

_USAGE_FAILURE_IDENTITY_KEYS = (
    "workspace_id",
    "run_id",
    "idempotency_key",
    "thread_id",
    "source",
    "input_tokens",
    "output_tokens",
    "model_id",
)


@dataclass(frozen=True)
class MiddlewareRegistration:
    """One host middleware factory and whether boot must abort on failure."""

    factory: MiddlewareFactory
    required: bool = True


_platform_quota_assert: Optional[PlatformQuotaAssert] = None
_usage_event_recorder: Optional[UsageEventRecorder] = None
_workspace_enricher: Optional[WorkspaceExportEnricher] = None
_background_task_factories: List[BackgroundTaskFactory] = []
_startup_readiness_checks: List[StartupReadinessCheck] = []
_server_managed_preferences: frozenset[str] = frozenset()
_host_tools: dict[tuple[str, str], HostToolHandler] = {}
_signature_vault_provider: Optional[SignatureVaultProvider] = None
_middleware_registrations: List[MiddlewareRegistration] = []
_entitlement_mutation_authorizer: Optional[EntitlementMutationAuthorizer] = None
_entitlement_access_resolver: Optional[EntitlementAccessResolver] = None
_model_price_resolver: Optional[ModelPriceResolver] = None
_model_bounds_resolver: Optional[ModelBoundsResolver] = None

_lock = threading.Lock()
_last_usage_record_failure: Optional[Dict[str, Any]] = None
_usage_record_failure_count: int = 0


def register_platform_quota_assert(fn: Optional[PlatformQuotaAssert]) -> None:
    """Register (or clear) the pre-operation platform quota gate."""
    global _platform_quota_assert
    _platform_quota_assert = fn


def register_model_price_resolver(fn: Optional[ModelPriceResolver]) -> None:
    """Trusted boot-time host registration; missing pricing is never a free quote."""
    global _model_price_resolver
    if fn is not None and not callable(fn):
        raise TypeError("model price resolver must be callable")
    _model_price_resolver = fn


def get_model_price_resolver() -> Optional[ModelPriceResolver]:
    """Used by the fail-closed work price service, never a public/model tool."""
    return _model_price_resolver


def register_model_bounds_resolver(fn: Optional[ModelBoundsResolver]) -> None:
    """Register trusted payload policy at host boot, never through an agent tool."""
    global _model_bounds_resolver
    if fn is not None and not callable(fn):
        raise TypeError("model bounds resolver must be callable")
    _model_bounds_resolver = fn


def get_model_bounds_resolver() -> Optional[ModelBoundsResolver]:
    """Missing host bounds must prevent approved bounded-work model dispatch."""
    return _model_bounds_resolver


def register_usage_event_recorder(fn: Optional[UsageEventRecorder]) -> None:
    """Register (or clear) the post-operation usage/event recorder."""
    global _usage_event_recorder
    _usage_event_recorder = fn


def register_workspace_export_enricher(fn: Optional[WorkspaceExportEnricher]) -> None:
    """Register (or clear) a workspace list/detail payload enricher."""
    global _workspace_enricher
    _workspace_enricher = fn


def register_startup_readiness_check(check: StartupReadinessCheck) -> None:
    """Register a required async pre-traffic check from the trusted host module."""
    if not callable(check):
        raise TypeError("startup readiness check must be callable")
    if check not in _startup_readiness_checks:
        _startup_readiness_checks.append(check)


async def assert_startup_ready() -> None:
    """Run required checks serially; an unavailable check prevents startup."""
    for check in tuple(_startup_readiness_checks):
        result = await check()
        if result is not None:
            raise TypeError("startup readiness check must return None or raise")


def register_server_managed_preference_keys(*keys: str) -> None:
    """Reserve host-owned preference slots from client writes and projections."""
    if any(not isinstance(key, str) or not key or key != key.strip() for key in keys):
        raise ValueError("Server-managed preference keys must be nonempty strings")
    global _server_managed_preferences
    _server_managed_preferences = _server_managed_preferences.union(keys)


def server_managed_preference_keys() -> frozenset[str]:
    """Return hidden and write-protected preferences declared by this host."""
    return _server_managed_preferences


@dataclass(frozen=True)
class _HostToolInvocation:
    ctx: Any
    actor: tuple[str, str, str]
    task: Any
    spec: Dict[str, Any]
    key: tuple[str, str]
    payload: Dict[str, Any]


_active_host_tool: ContextVar[Optional[_HostToolInvocation]] = ContextVar(
    "integral_active_host_tool", default=None
)


def register_host_tool(bundle_slug: str, tool_key: str, fn: HostToolHandler) -> None:
    """Bind a trusted host action to one installed bundle's exact write tool."""
    key = (bundle_slug, tool_key)
    if any(
        not isinstance(part, str) or not part or part != part.strip() for part in key
    ):
        raise ValueError("Host tool identity must be nonempty")
    if not callable(fn):
        raise TypeError("Host tool handler must be callable")
    if key in _host_tools and _host_tools[key] is not fn:
        raise RuntimeError("Host tool is already bound")
    _host_tools[key] = fn


def _ctx_actor(ctx: Any) -> tuple[str, str, str]:
    return ctx.user_id, ctx.workspace_id, ctx.scope


def _registered_host_tool_spec(ctx: Any, spec: Dict[str, Any], tool_key: str) -> bool:
    from app.services.hooks.registry import get_workspace_tools

    return get_workspace_tools(ctx.workspace_id).get(tool_key) is spec


@contextmanager
def bound_host_tool(
    spec: Dict[str, Any], payload: Dict[str, Any], ctx: Any
) -> Iterator[None]:
    """Select a host action only from the current registry-owned dispatch."""
    key = (str(spec.get("_bundle_slug") or ""), str(spec.get("key") or ""))
    binding = None
    if (
        key in _host_tools
        and ctx.actor_kind == "human"
        and not ctx.read_only
        and ctx.user_id
        and ctx.workspace_id
        and ctx.tool_is_write
        and _registered_host_tool_spec(ctx, spec, key[1])
    ):
        binding = _HostToolInvocation(
            ctx,
            _ctx_actor(ctx),
            asyncio.current_task(),
            spec,
            key,
            copy.deepcopy(payload),
        )
    token = _active_host_tool.set(binding)
    try:
        yield
    finally:
        _active_host_tool.reset(token)


async def run_bound_host_tool(ctx: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke only the active, exact, validated tool; never a supplied name."""
    from app.api.errors import InsufficientPermissionsError

    bound = _active_host_tool.get()
    if (
        bound is None
        or bound.ctx is not ctx
        or bound.task is not asyncio.current_task()
        or bound.actor != _ctx_actor(ctx)
        or ctx.actor_kind != "human"
        or ctx.read_only
        or not ctx.tool_is_write
        or bound.payload != payload
        or not _registered_host_tool_spec(ctx, bound.spec, bound.key[1])
    ):
        raise InsufficientPermissionsError(
            message="A current registered human write tool is required"
        )
    _active_host_tool.set(None)  # One invocation under this exact write dispatch.
    return await _host_tools[bound.key](copy.deepcopy(bound.payload), ctx)


def register_signature_vault_provider(fn: SignatureVaultProvider) -> None:
    """Bind an optional host adapter for signed-in users' signature vault."""
    global _signature_vault_provider
    if not callable(fn):
        raise TypeError("Signature vault provider must be callable")
    if _signature_vault_provider is not None and _signature_vault_provider is not fn:
        raise RuntimeError("Signature vault provider is already bound")
    _signature_vault_provider = fn


async def invoke_signature_vault_provider(
    operation: SignatureVaultOperation,
    workspace_id: str,
    actor_user_id: str,
    payload: Dict[str, Any],
) -> SignatureVaultResult:
    """Invoke trusted code with the actor and workspace validated by the route.

    No registered provider, or its explicit None result for a workspace where
    it is not installed, permits fallback to Core's own vault. Providers must
    raise on denial or failure. Malformed results never change storage paths.
    """
    if _signature_vault_provider is None:
        return None
    result = await _signature_vault_provider(
        operation, workspace_id, actor_user_id, payload
    )
    if result is None:
        return None
    expected = {"list": list, "save": dict, "delete": bool}[operation]
    if not isinstance(result, expected):
        from app.exceptions import AuthorizationUnavailableError

        raise AuthorizationUnavailableError()
    return result


def register_background_task(factory: BackgroundTaskFactory) -> None:
    """Append a coroutine factory started once during API boot."""
    if factory not in _background_task_factories:
        _background_task_factories.append(factory)


def register_middleware_factory(
    factory: MiddlewareFactory, *, required: bool = True
) -> None:
    """Append a Starlette middleware class factory for API boot.

    ``required=True`` (default): a factory exception aborts boot.
    ``required=False``: log a warning and continue mounting later factories.
    Returning ``None`` from the factory skips that entry without failing.
    """
    for reg in _middleware_registrations:
        if reg.factory is factory:
            return
    _middleware_registrations.append(
        MiddlewareRegistration(factory=factory, required=bool(required))
    )


def register_entitlement_mutation_authorizer(
    fn: Optional[EntitlementMutationAuthorizer],
) -> None:
    """Register (or clear) host policy for entitlement grant/revoke mutations."""
    global _entitlement_mutation_authorizer
    _entitlement_mutation_authorizer = fn


def register_entitlement_access_resolver(
    fn: Optional[EntitlementAccessResolver],
) -> None:
    """Register trusted host policy without minting durable access grants."""
    global _entitlement_access_resolver
    if fn is not None and not callable(fn):
        raise TypeError("entitlement access resolver must be callable")
    _entitlement_access_resolver = fn


async def host_allows_entitlement_access(
    workspace_id: str, entitlement_key: str, package_slug: str
) -> bool:
    """Only an explicit True overrides access; errors never grant access."""
    if _entitlement_access_resolver is None:
        return False
    result = await _entitlement_access_resolver(
        workspace_id, entitlement_key, package_slug
    )
    if not isinstance(result, bool):
        raise TypeError("entitlement access resolver must return bool")
    return result


async def assert_entitlement_mutation_allowed(
    request: Any, action: str, workspace_id: str
) -> None:
    """No-op unless a host registered an entitlement-mutation authorizer."""
    if _entitlement_mutation_authorizer is None:
        return
    await _entitlement_mutation_authorizer(request, action, workspace_id)


def list_background_task_factories() -> List[BackgroundTaskFactory]:
    """Return registered background-task factories (copy)."""
    return list(_background_task_factories)


def list_middleware_factories() -> List[MiddlewareFactory]:
    """Return registered middleware factories (copy)."""
    return [reg.factory for reg in _middleware_registrations]


def list_middleware_registrations() -> List[MiddlewareRegistration]:
    """Return registered middleware factories with required flags (copy)."""
    return list(_middleware_registrations)


def mount_host_middleware(
    app: Any,
    registrations: Optional[Sequence[MiddlewareRegistration]] = None,
) -> List[Any]:
    """Mount host middleware factories onto ``app``.

    Required factory failures abort with ``RuntimeError``. Optional failures
    are logged and skipped so later factories still run. A factory that
    returns ``None`` is skipped without error.
    """
    regs = (
        list(registrations)
        if registrations is not None
        else list_middleware_registrations()
    )
    mounted: List[Any] = []
    for reg in regs:
        try:
            middleware = reg.factory()
        except Exception as exc:  # noqa: BLE001
            if reg.required:
                logger.error(
                    "required host middleware failed to mount: %s",
                    exc,
                    exc_info=True,
                )
                raise RuntimeError(
                    f"required host middleware failed to mount: {exc}"
                ) from exc
            logger.warning(
                "optional host middleware failed to mount: %s",
                exc,
                exc_info=True,
            )
            continue
        if middleware is None:
            continue
        app.add_middleware(middleware)
        mounted.append(middleware)
        logger.info(
            "host middleware mounted: %s",
            getattr(middleware, "__name__", repr(middleware)),
        )
    return mounted


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


def _usage_failure_snapshot(
    exc: BaseException, kwargs: Dict[str, Any]
) -> Dict[str, Any]:
    """Build an actionable failure snapshot with replay identity fields."""
    snapshot: Dict[str, Any] = {
        "error": f"{type(exc).__name__}: {exc}",
        "kwargs_keys": sorted(str(k) for k in kwargs.keys()),
    }
    for key in _USAGE_FAILURE_IDENTITY_KEYS:
        if key in kwargs and kwargs[key] is not None:
            snapshot[key] = kwargs[key]
    if "workspace_id" not in snapshot:
        snapshot["workspace_id"] = kwargs.get("workspace_id")
    return snapshot


async def assert_platform_quota(workspace_id: str) -> None:
    """Authorization: no-op unless a host registered a quota gate."""
    if _platform_quota_assert is None:
        return
    await _platform_quota_assert(workspace_id)


async def record_usage_event(**kwargs: Any) -> Any:
    """Metering: no-op unless a host registered a usage recorder.

    Failures are logged and retained via ``get_last_usage_record_failure`` so
    they are observable/recoverable. The caller's request path is not aborted
    (authorization already ran separately via ``assert_platform_quota``).
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
            _last_usage_record_failure = _usage_failure_snapshot(exc, kwargs)
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
