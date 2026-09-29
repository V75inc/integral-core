"""Hosted billing status and the operator projection write. No Stripe SDK."""

from typing import List

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import require_platform_admin, resolve_principal_id
from app.config import settings
from app.models.hosted_subscription import HostedSubscription
from app.models.nodes import Workspace
from app.schemas.billing import (
    BillingStatusResponse,
    HostedSubscriptionListResponse,
    HostedSubscriptionResponse,
    HostedSubscriptionUpsertRequest,
)
from app.services.hosted_subscription import (
    access_for_row,
    find_hosted_subscription,
    get_billing_manual_apply,
    grace_until_iso,
    upsert_hosted_subscription,
)
from app.services.workspace_permissions import is_workspace_admin_or_owner


async def _workspace_name(workspace_id: str) -> str:
    ws = (workspace_id or "").strip()
    if not ws:
        return ""
    try:
        node = await Workspace.get(ws)
    except Exception:  # noqa: BLE001
        return ""
    if node is None:
        return ""
    return str(getattr(node, "name", None) or "").strip()


async def _row_response(row: HostedSubscription) -> HostedSubscriptionResponse:
    return HostedSubscriptionResponse(
        workspace_id=row.workspace_id,
        workspace_name=await _workspace_name(row.workspace_id),
        billing_account_id=row.billing_account_id or "",
        status=row.status,
        plan_key=row.plan_key or "basic",
        source=row.source or "manual",
        external_customer_id=row.external_customer_id or "",
        external_subscription_id=row.external_subscription_id or "",
        past_due_since=row.past_due_since,
        access_until=getattr(row, "access_until", None),
        current_period_end=getattr(row, "current_period_end", None),
        cancel_at_period_end=bool(getattr(row, "cancel_at_period_end", False)),
        access=access_for_row(row),
        grace_until=grace_until_iso(row.past_due_since),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _status_payload(workspace_id: str, row) -> BillingStatusResponse:
    if not settings.INTEGRAL_SUBSCRIPTION_REQUIRED:
        return BillingStatusResponse(
            subscription_required=False,
            access="off",
            workspace_id=workspace_id,
            checkout_available=bool((settings.INTEGRAL_BILLING_MODULE or "").strip()),
        )
    access = access_for_row(row)
    return BillingStatusResponse(
        subscription_required=True,
        access=access,
        status=getattr(row, "status", None) if row else None,
        plan_key=getattr(row, "plan_key", None) if row else None,
        billing_account_id=getattr(row, "billing_account_id", None) if row else None,
        workspace_id=workspace_id,
        grace_until=(
            grace_until_iso(getattr(row, "past_due_since", None)) if row else None
        ),
        source=getattr(row, "source", None) if row else None,
        checkout_available=bool((settings.INTEGRAL_BILLING_MODULE or "").strip()),
        current_period_end=(getattr(row, "current_period_end", None) if row else None),
        cancel_at_period_end=(
            bool(getattr(row, "cancel_at_period_end", False)) if row else False
        ),
    )


async def _require_workspace_admin(request: Request, workspace_id: str) -> str:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    from app.api.utils import is_platform_admin

    if is_platform_admin(request):
        return user_id
    if await is_workspace_admin_or_owner(user_id, workspace_id):
        return user_id
    from app.api.errors import InsufficientPermissionsError

    raise InsufficientPermissionsError(
        message="billing status requires workspace admin/owner",
        details={"workspace_id": workspace_id},
    )


@endpoint("/billing/status", methods=["GET"], auth=True, tags=["Billing"])
async def get_billing_status(
    request: Request,
    workspace_id: str = "",
) -> BillingStatusResponse:
    """Explain the hosted base plan. Does not grant access by itself."""
    ws = (workspace_id or "").strip()
    if not ws:
        raise BadRequestError(message="workspace_id query param is required")
    await _require_workspace_admin(request, ws)
    row = (
        await find_hosted_subscription(ws)
        if settings.INTEGRAL_SUBSCRIPTION_REQUIRED
        else None
    )
    return _status_payload(ws, row)


@endpoint("/billing/subscription", methods=["POST"], auth=True, tags=["Billing"])
async def post_billing_subscription(
    request: Request,
    workspace_id: str = "",
    billing_account_id: str = "",
    status: str = "",
    plan_key: str = "basic",
    external_customer_id: str = "",
    external_subscription_id: str = "",
    past_due_since: str = "",
    access_until: str = "",
) -> HostedSubscriptionResponse:
    """Operator override for the base plan. Stripe reconcile will not clobber it."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    require_platform_admin(request)
    # Prefer JSON body when present (admin console).
    try:
        raw = await request.json()
    except Exception:  # noqa: BLE001
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    body = HostedSubscriptionUpsertRequest(
        workspace_id=str(raw.get("workspace_id") or workspace_id or "").strip(),
        billing_account_id=str(
            raw.get("billing_account_id") or billing_account_id or ""
        ).strip(),
        status=str(raw.get("status") or status or "").strip(),
        plan_key=str(raw.get("plan_key") or plan_key or "basic").strip() or "basic",
        external_customer_id=str(
            raw.get("external_customer_id") or external_customer_id or ""
        ).strip(),
        external_subscription_id=str(
            raw.get("external_subscription_id") or external_subscription_id or ""
        ).strip(),
        past_due_since=(
            str(raw.get("past_due_since") or past_due_since or "").strip() or None
        ),
        access_until=(
            str(raw.get("access_until") or access_until or "").strip() or None
        ),
    )
    if not body.workspace_id:
        raise BadRequestError(message="workspace_id is required")
    if not body.status:
        raise BadRequestError(message="status is required")
    clear_until = "access_until" in raw and not (
        str(raw.get("access_until") or "").strip()
    )
    row, _applied = await upsert_hosted_subscription(
        workspace_id=body.workspace_id,
        billing_account_id=body.billing_account_id or f"ba:{body.workspace_id}",
        status=body.status,
        plan_key=body.plan_key,
        source="manual",
        external_customer_id=body.external_customer_id,
        external_subscription_id=body.external_subscription_id,
        past_due_since=body.past_due_since,
        access_until=body.access_until,
        clear_access_until=clear_until,
        from_provider=False,
    )
    hook = get_billing_manual_apply()
    if hook is not None:
        await hook(body.workspace_id, body.plan_key, body.status)
    return await _row_response(row)


@endpoint("/billing/subscriptions", methods=["GET"], auth=True, tags=["Billing"])
async def list_billing_subscriptions(
    request: Request,
    status: str = "",
    source: str = "",
) -> HostedSubscriptionListResponse:
    """Platform-admin index of every hosted base-plan projection."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    require_platform_admin(request)

    status_filter = (status or "").strip().lower()
    source_filter = (source or "").strip().lower()
    query: dict = {}
    if status_filter:
        query["context.status"] = status_filter
    if source_filter:
        query["context.source"] = source_filter

    rows: List[HostedSubscription] = list(await HostedSubscription.find(query) or [])
    # Prefer updated_at descending so the freshest operator work is on top.
    rows.sort(
        key=lambda row: (getattr(row, "updated_at", None) or ""),
        reverse=True,
    )
    items = [await _row_response(row) for row in rows]
    return HostedSubscriptionListResponse(subscriptions=items, total=len(items))
