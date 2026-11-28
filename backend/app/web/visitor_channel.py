"""Decide which channel id may serve a visitor surface."""

from fastapi import Request


def accepted_visitor_slug(request: Request, slug: str) -> str | None:
    """Return the channel id when it may serve visitors, else None."""
    from app.agentive.services.web_chat_package import SLUG
    from app.connectors.channel_packages import ChannelPackageRegistry

    state = getattr(getattr(request, "app", None), "state", None)
    registry = getattr(state, "integral_managed_channel_registry", None)
    if isinstance(registry, ChannelPackageRegistry):
        package = registry.resolve(slug)
        if package is None or package.definition.visitor_surface is not True:
            return None
        return slug
    if slug != SLUG:
        return None
    return slug
