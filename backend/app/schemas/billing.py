"""Schemas for the hosted base-plan projection. No Stripe types."""

from typing import Optional

from pydantic import BaseModel


class BillingStatusResponse(BaseModel):
    subscription_required: bool
    # off | open | grace | locked
    access: str
    workspace_id: str
    status: Optional[str] = None
    plan_key: Optional[str] = None
    billing_account_id: Optional[str] = None
    grace_until: Optional[str] = None
    source: Optional[str] = None
    checkout_available: bool = False
    current_period_end: Optional[str] = None
    cancel_at_period_end: bool = False

    model_config = {"extra": "forbid"}


class HostedSubscriptionUpsertRequest(BaseModel):
    workspace_id: str
    status: str
    billing_account_id: str = ""
    plan_key: str = "basic"
    external_customer_id: str = ""
    external_subscription_id: str = ""
    past_due_since: Optional[str] = None
    access_until: Optional[str] = None

    model_config = {"extra": "forbid"}


class HostedSubscriptionResponse(BaseModel):
    workspace_id: str
    workspace_name: str = ""
    billing_account_id: str
    status: str
    plan_key: str
    source: str
    external_customer_id: str
    external_subscription_id: str
    past_due_since: Optional[str] = None
    access_until: Optional[str] = None
    current_period_end: Optional[str] = None
    cancel_at_period_end: bool = False
    access: str
    grace_until: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = {"extra": "forbid"}


class HostedSubscriptionListResponse(BaseModel):
    subscriptions: list[HostedSubscriptionResponse]
    total: int

    model_config = {"extra": "forbid"}
