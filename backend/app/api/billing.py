"""Hosted billing status and the operator projection write. No Stripe SDK."""

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import require_platform_admin, resolve_principal_id
from app.config import settings
from app.schemas.billing import (
    BillingStatusResponse,
    HostedSubscriptionResponse,
    HostedSubscriptionUpsertRequest,
)
from app.services.hosted_subscription import (
    access_for_row,
    find_hosted_subscription,
    grace_until_iso,
    upsert_hosted_subscription,
)
from app.services.workspace_permissions import is_workspace_admin_or_owner


def _status_payload(workspace_id: str, row) -> BillingStatusResponse:
    if not settings.INTEGRAL_HOSTED:
        return BillingStatusResponse(
            hosted=False,
            access="unhosted",
            workspace_id=workspace_id,
            checkout_available=bool((settings.INTEGRAL_BILLING_MODULE or "").strip()),
        )
    access = access_for_row(row)
    return BillingStatusResponse(
        hosted=True,
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
    row = await find_hosted_subscription(ws) if settings.INTEGRAL_HOSTED else None
    return _status_payload(ws, row)


@endpoint("/billing/subscription", methods=["POST"], auth=True, tags=["Billing"])
async def post_billing_subscription(
    request: Request,
    workspace_id: str = "",
    billing_account_id: str = "",
    status: str = "",
    plan_key: str = "base",
    external_customer_id: str = "",
    external_subscription_id: str = "",
    past_due_since: str = "",
) -> HostedSubscriptionResponse:
    """Operator override for the base plan. Stripe reconcile will not clobber it."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    require_platform_admin(request)
    body = HostedSubscriptionUpsertRequest(
        workspace_id=workspace_id,
        billing_account_id=billing_account_id,
        status=status,
        plan_key=plan_key or "base",
        external_customer_id=external_customer_id or "",
        external_subscription_id=external_subscription_id or "",
        past_due_since=past_due_since or None,
    )
    row, _applied = await upsert_hosted_subscription(
        workspace_id=body.workspace_id,
        billing_account_id=body.billing_account_id,
        status=body.status,
        plan_key=body.plan_key,
        source="manual",
        external_customer_id=body.external_customer_id,
        external_subscription_id=body.external_subscription_id,
        past_due_since=body.past_due_since,
        from_stripe=False,
    )
    return HostedSubscriptionResponse(
        workspace_id=row.workspace_id,
        billing_account_id=row.billing_account_id or "",
        status=row.status,
        plan_key=row.plan_key or "base",
        source=row.source or "manual",
        external_customer_id=row.external_customer_id or "",
        external_subscription_id=row.external_subscription_id or "",
        past_due_since=row.past_due_since,
        access=access_for_row(row),
    )
