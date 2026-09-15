"""Regression: jvagent's process-wide caches must not cross-serve overlays.

``discover_skill_docs`` caches the MERGED skill list (filesystem docs plus
host-provider docs) keyed only on app root / agent / mtime, and the tool-surface
cache is keyed on the single resident agent id. Integral's host provider answers
from a per-(workspace, user) ContextVar, so a retained entry is cross-tenant by
construction: workspace A's overlay is served to workspace B.

Clearing the caches at the top of each turn is not enough once two turns
overlap — A clears, B clears, A writes, B reads A's entry. The caches must be
unable to retain an entry at all.
"""

from __future__ import annotations

import pytest

from app.services.chat_providers.jvagent_provider import JvagentProvider

pytestmark = pytest.mark.smoke


@pytest.fixture(scope="module", autouse=True)
def _boot_app_before_touching_jvagent():
    """Boot the app before this module imports jvagent's orchestrator.

    Importing ``jvagent.action.orchestrator.*`` transitively imports
    ``jvagent.action.interact``, whose ``@endpoint`` decorators mount routes on
    the shared jvspatial app. ``app.main``'s boot guard refuses to start once
    those routes exist, so a sibling test calling ``get_app()`` after this
    module would fail. Booting first keeps the guard's contract intact and
    isolates the ordering effect to this file.
    """
    from tests.conftest import get_app

    get_app()


def test_overlay_caches_are_neutralized():
    """After install, neither jvagent cache retains what is written to it."""
    assert JvagentProvider._disable_jvagent_overlay_caches() is True

    from jvagent.action.orchestrator import catalog as jv_catalog
    from jvagent.action.orchestrator import skills as jv_skills

    # A write followed by a read must miss — this is what makes overlapping
    # turns safe, where a plain .clear() would race.
    jv_skills._SKILL_DISCOVERY_CACHE[("ws_a", "user_a")] = ["workspace-a-overlay"]
    assert jv_skills._SKILL_DISCOVERY_CACHE.get(("ws_a", "user_a")) is None

    jv_catalog._TOOL_SURFACE_CACHE["resident-agent"] = object()
    assert jv_catalog._TOOL_SURFACE_CACHE.get("resident-agent") is None


def test_neutralized_caches_still_support_clear_and_pop():
    """Jvagent's own invalidation helpers must keep working against them."""
    JvagentProvider._disable_jvagent_overlay_caches()

    from jvagent.action.orchestrator.catalog import invalidate_tool_surface_cache
    from jvagent.action.orchestrator.skills import clear_skill_discovery_cache

    # Neither should raise against the replacement mappings.
    clear_skill_discovery_cache()
    invalidate_tool_surface_cache("resident-agent")
    invalidate_tool_surface_cache(None)


def test_install_is_idempotent():
    """Repeated installs must not stack wrappers or lose the neutralization."""
    from jvagent.action.orchestrator import skills as jv_skills

    JvagentProvider._disable_jvagent_overlay_caches()
    first = jv_skills._SKILL_DISCOVERY_CACHE
    JvagentProvider._disable_jvagent_overlay_caches()
    assert jv_skills._SKILL_DISCOVERY_CACHE is first
