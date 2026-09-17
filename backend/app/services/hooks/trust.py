"""Trust-tier gate for tools[] (DR-30-01)."""

from app.services.hooks.errors import ToolTrustTierDeniedError

_TRUSTED_TIERS = frozenset({"trusted", "audited"})


def check_tools_permitted(trust_tier: str, tools_count: int, bundle_slug: str) -> None:
    """Raise ToolTrustTierDeniedError if the bundle declares tools without a trusted tier."""
    if tools_count == 0:
        return
    if (trust_tier or "").lower() in _TRUSTED_TIERS:
        return
    raise ToolTrustTierDeniedError(
        message=(
            f"bundle {bundle_slug!r} declares {tools_count} tools but "
            f"trust_tier={trust_tier!r}. Tools require trusted bundle."
        ),
        details={
            "bundle_slug": bundle_slug,
            "trust_tier": trust_tier,
            "tools_count": tools_count,
        },
    )


def check_operations_permitted(
    trust_tier: str, operations_count: int, bundle_slug: str
) -> None:
    """Raise ToolTrustTierDeniedError if the bundle declares operations without trust."""
    if operations_count == 0:
        return
    if (trust_tier or "").lower() in _TRUSTED_TIERS:
        return
    raise ToolTrustTierDeniedError(
        message=(
            f"bundle {bundle_slug!r} declares {operations_count} operations but "
            f"trust_tier={trust_tier!r}. Operations require trusted bundle."
        ),
        details={
            "bundle_slug": bundle_slug,
            "trust_tier": trust_tier,
            "operations_count": operations_count,
        },
    )
