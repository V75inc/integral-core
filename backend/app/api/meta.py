"""Meta API endpoints for templates and configuration."""

import asyncio
import logging
from typing import Any, Dict

from jvspatial.api import endpoint

from app.api.errors import ServiceUnavailableError

logger = logging.getLogger(__name__)

# Readiness probe budget. Tuned to be shorter than typical Fly check timeout
# (5s) so a slow DB surfaces as 503 instead of timing out the check itself.
_READINESS_DB_TIMEOUT_SECONDS = 2.0


@endpoint("/health/ready", methods=["GET"], auth=False, tags=["Meta"])
async def readiness() -> Dict[str, Any]:
    """Readiness probe — succeeds only when the backend can serve traffic.

    Pings the jvspatial prime database via a cheap count on a sentinel
    collection (returns 0 for missing collections across SQLite, JsonDB,
    and Mongo adapters). Returns 503 if the database is unreachable or
    the probe exceeds `_READINESS_DB_TIMEOUT_SECONDS`.

    Intended for a load balancer's HTTP service check. Liveness is
    handled by jvspatial's built-in `/health` route, which the Dockerfile
    HEALTHCHECK targets and does its own `Root.get()` probe. This route
    exists because `Root.get()` is heavier than `count()` and we want a
    faster, more predictable check in front of a rolling deploy.

    No deployed stack wires this up today — the Fly.io service check that
    used to consume it went away with `backend/fly.toml`. Kept because the
    probe is deploy-target-agnostic and the Swarm stacks can adopt it.
    """
    from jvspatial.db import get_database_manager

    try:
        db = get_database_manager().get_prime_database()
        await asyncio.wait_for(
            db.count("__readiness_probe__", {}),
            timeout=_READINESS_DB_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Readiness probe timed out after %.1fs", _READINESS_DB_TIMEOUT_SECONDS
        )
        raise ServiceUnavailableError(
            message="Readiness probe timed out",
            details={"status": "unavailable", "reason": "db_timeout"},
        )
    except Exception as exc:  # noqa: BLE001 — surface any DB failure as 503
        logger.warning("Readiness probe failed: %s: %s", type(exc).__name__, exc)
        raise ServiceUnavailableError(
            message="Readiness probe failed",
            details={"status": "unavailable", "reason": "db_error"},
        )

    from app.modules import intelligence_runtime_status

    intelligence = intelligence_runtime_status()
    return {
        "status": "ready",
        "intelligence": {
            "available": intelligence.available,
            "reason": intelligence.reason or None,
        },
    }


@endpoint("/meta/build", methods=["GET"], auth=False, tags=["Meta"])
async def build_meta() -> Dict[str, Any]:
    """Public asset-version probe for stale SPA tabs.

    The frontend compares ``asset_version`` to its baked
    ``VITE_WEB_ASSET_VERSION``. On mismatch it auto-reloads once (see
    ``useBuildVersionWatch``). Keep API + SPA env in lockstep on deploy.
    """
    from app.config import settings

    return {"asset_version": settings.WEB_ASSET_VERSION}


@endpoint("/meta/templates", methods=["GET"], tags=["Meta"])
async def get_templates() -> Dict[str, Any]:
    """Get track templates configuration.

    Returns template definitions with names, tags, and color schemes.
    This is currently hardcoded but could be made configurable.

    Returns:
        List of template configurations
    """
    templates = [
        {
            "name": "Project",
            "tags": ["Planning", "Development", "Launch"],
            "colors": {"text": "#1F2937", "bg": "#F3F4F6"},
        },
        {
            "name": "Personal Goals",
            "tags": ["Health", "Career", "Learning"],
            "colors": {"text": "#065F46", "bg": "#D1FAE5"},
        },
        {
            "name": "Finances",
            "tags": ["Income", "Expenses", "Investments"],
            "colors": {"text": "#7C2D12", "bg": "#FED7AA"},
        },
        {
            "name": "Health & Fitness",
            "tags": ["Workout", "Nutrition", "Mental Health"],
            "colors": {"text": "#991B1B", "bg": "#FECACA"},
        },
        {
            "name": "Learning",
            "tags": ["Courses", "Books", "Practice"],
            "colors": {"text": "#1E40AF", "bg": "#DBEAFE"},
        },
        {
            "name": "Travel",
            "tags": ["Planning", "Destinations", "Budget"],
            "colors": {"text": "#6D28D9", "bg": "#E9D5FF"},
        },
        {
            "name": "Content Creation",
            "tags": ["Ideas", "Drafts", "Published"],
            "colors": {"text": "#BE185D", "bg": "#FBE7F3"},
        },
        {
            "name": "Home & Chores",
            "tags": ["Cleaning", "Maintenance", "Shopping"],
            "colors": {"text": "#92400E", "bg": "#FEF3C7"},
        },
        {
            "name": "Creative Projects",
            "tags": ["Ideas", "In Progress", "Completed"],
            "colors": {"text": "#BE123C", "bg": "#FFE4E6"},
        },
        {
            "name": "Collections",
            "tags": ["Items", "Categories", "Wishlist"],
            "colors": {"text": "#0F766E", "bg": "#CCFBF1"},
        },
        {
            "name": "Event Planning",
            "tags": ["Guests", "Venue", "Schedule"],
            "colors": {"text": "#7E22CE", "bg": "#F3E8FF"},
        },
        {
            "name": "Media Library",
            "tags": ["Movies", "Books", "Music"],
            "colors": {"text": "#0369A1", "bg": "#BAE6FD"},
        },
    ]

    return {"templates": templates, "total": len(templates)}


@endpoint("/meta/entry-type-icons", methods=["GET"], tags=["Meta"])
async def get_entry_type_icons() -> Dict[str, Any]:
    """Get available entry type icons.

    Returns a mapping of icon names that can be used for entry types.

    Returns:
        List of available icon names
    """
    icons = [
        "document",
        "folder",
        "briefcase",
        "chart",
        "calendar",
        "user",
        "users",
        "mail",
        "bell",
        "bookmark",
        "tag",
        "paperclip",
        "camera",
        "image",
        "film",
        "music",
        "heart",
        "star",
        "flag",
        "check-circle",
        "x-circle",
        "exclamation",
        "question",
        "info",
        "light-bulb",
        "sparkles",
        "fire",
        "trophy",
        "gift",
        "shopping-cart",
        "credit-card",
        "cash",
        "home",
        "building",
        "map",
        "globe",
        "phone",
        "chat",
        "code",
        "terminal",
        "database",
        "server",
        "cloud",
        "download",
        "upload",
        "link",
        "lock",
        "unlock",
        "shield",
        "cog",
        "wrench",
        "pencil",
        "trash",
        "archive",
        "clipboard",
        "search",
        "filter",
        "sort",
        "refresh",
    ]

    return {"icons": icons, "total": len(icons)}


@endpoint("/meta/colors", methods=["GET"], tags=["Meta"])
async def get_color_palettes() -> Dict[str, Any]:
    """Get predefined color palettes for tags and categories.

    Returns color schemes that can be used throughout the application.

    Returns:
        Color palette definitions
    """
    palettes = {
        "primary": [
            {"name": "Blue", "hex": "#3B82F6"},
            {"name": "Indigo", "hex": "#6366F1"},
            {"name": "Purple", "hex": "#8B5CF6"},
            {"name": "Pink", "hex": "#EC4899"},
            {"name": "Red", "hex": "#EF4444"},
            {"name": "Orange", "hex": "#F97316"},
            {"name": "Yellow", "hex": "#F59E0B"},
            {"name": "Green", "hex": "#10B981"},
            {"name": "Teal", "hex": "#14B8A6"},
            {"name": "Cyan", "hex": "#06B6D4"},
        ],
        "neutral": [
            {"name": "Gray 400", "hex": "#9CA3AF"},
            {"name": "Gray 500", "hex": "#6B7280"},
            {"name": "Gray 600", "hex": "#4B5563"},
            {"name": "Gray 700", "hex": "#374151"},
        ],
        "pastel": [
            {"name": "Pastel Blue", "hex": "#DBEAFE"},
            {"name": "Pastel Green", "hex": "#D1FAE5"},
            {"name": "Pastel Yellow", "hex": "#FEF3C7"},
            {"name": "Pastel Red", "hex": "#FECACA"},
            {"name": "Pastel Purple", "hex": "#E9D5FF"},
            {"name": "Pastel Pink", "hex": "#FBE7F3"},
        ],
    }

    return {"palettes": palettes}
