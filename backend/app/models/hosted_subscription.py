"""HostedSubscription — base-plan projection for a hosted workspace (F3 / I-GRAPH-02).

Object, not Node: one record per workspace. Stripe is not imported here.
The Business control plane writes this row. Open-source installs leave
``INTEGRAL_HOSTED`` unset and never read it.
"""

from __future__ import annotations

from typing import Optional

from jvspatial.core import Object
from jvspatial.core.annotations import attribute, compound_index


@compound_index(
    [("workspace_id", 1)],
    name="hosted_subscription_ws",
    unique=True,
    partial_filter_expression={
        "entity": "HostedSubscription",
        "context.workspace_id": {"$gt": ""},
    },
)
class HostedSubscription(Object):
    """One workspace's hosted base-plan projection."""

    workspace_id: str = attribute(default="", indexed=True)
    billing_account_id: str = attribute(default="", indexed=True)
    # trialing | active | past_due | canceled | incomplete
    status: str = attribute(default="incomplete", indexed=True)
    plan_key: str = attribute(default="base")
    # manual rows are operator overrides and are not overwritten by Stripe.
    source: str = attribute(default="manual")
    external_customer_id: str = attribute(default="")
    external_subscription_id: str = attribute(default="")
    past_due_since: Optional[str] = attribute(default=None)
    created_at: Optional[str] = attribute(default=None)
    updated_at: Optional[str] = attribute(default=None)
