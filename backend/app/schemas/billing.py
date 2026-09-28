"""Schemas for the hosted base-plan projection. No Stripe types."""

from typing import Optional

from pydantic import BaseModel


class BillingStatusResponse(BaseModel):
    hosted: bool
    # unhosted | open | grace | locked
    access: str
    workspace_id: str
    status: Optional[str] = None
    plan_key: Optional[str] = None
    billing_account_id: Optional[str] = None
    grace_until: Optional[str] = None
    source: Optional[str] = None
    checkout_available: bool = False

    model_config = {"extra": "forbid"}


class HostedSubscriptionUpsertRequest(BaseModel):
    workspace_id: str
    status: str
    billing_account_id: str = ""
    plan_key: str = "base"
    external_customer_id: str = ""
    external_subscription_id: str = ""
    past_due_since: Optional[str] = None

    model_config = {"extra": "forbid"}


class HostedSubscriptionResponse(BaseModel):
    workspace_id: str
    billing_account_id: str
    status: str
    plan_key: str
    source: str
    external_customer_id: str
    external_subscription_id: str
    past_due_since: Optional[str] = None
    access: str

    model_config = {"extra": "forbid"}
