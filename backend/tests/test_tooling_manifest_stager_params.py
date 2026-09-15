"""The manifest-published param schema MUST match what the propose stagers consume.

Regression guard for the schema/stager drift where the manifest advertised
``integral_modify_profile`` as ``{draft_id, high_level_changes}`` while
``_stage_modify_profile`` required ``{action, track_id|app_id, ...}`` — so an
agent calling the tool per its *advertised* schema always hit the stager's
fail-closed ``ValueError`` (likewise ``integral_author_profile`` advertised
``{profile_id, instructions}`` against the stager's ``description``).

Two contracts per reconciled tool (see
:data:`app.agentive.tooling.bindings.STAGER_ACCEPTED_PARAMS`):

1. NAME-SUBSET — every manifest-published param name is one the stager accepts.
   Catches a newly-advertised param the stager would reject or silently drop.
2. BEHAVIORAL — feeding the stager the FULL published param surface (sample
   values) stages cleanly (returns ``{kind, payload, ...}`` without raising).
   This is the end-to-end proof an agent calling exactly per the advertised
   schema is staged — the exact path that was fail-closed before the fix.

Both run against ``build_tool_catalogue()`` (what the resident surface + MCP
server actually publish), not the raw YAML, so a regression in the catalogue
builder is caught too.
"""

from __future__ import annotations

import inspect

import pytest

from app.agentive.tooling import build_tool_catalogue
from app.agentive.tooling.bindings import STAGER_ACCEPTED_PARAMS, TOOL_BINDINGS

# Tools whose manifest schema was reconciled against the stager. Keyed per-tool
# so a failure names the offending tool.
RECONCILED_TOOLS = sorted(STAGER_ACCEPTED_PARAMS)

# Type-appropriate sample values for every param these reconciled tools publish.
# A required param with no sample is a test gap, so the behavioral check asserts
# coverage (fails loud rather than silently skipping a param).
_SAMPLE_VALUES = {
    # modify_profile
    "action": "add_entry_type",
    "track_id": "n.Track.sample",
    "app_id": "n.WorkspaceApp.sample",
    "space_id": "n.WorkspaceApp.sample",
    "name": "Sample",
    "icon": "star",
    "view_type": "feed",
    "config": {"group_by": "status"},
    "color": "#abcdef",
    "group_key": "status",
    "entry_type_id": "n.EntryType.sample",
    "view_id": "n.View.sample",
    "tag_id": "n.Tag.sample",
    # author_profile
    "description": "An app for tracking records and items",
    "scope": "track",
    "instructions": "Track contacts and deals",
}


def _catalogue_schema(name: str) -> dict:
    """Return the published ``input_schema`` for ``name`` from the catalogue."""
    for tool in build_tool_catalogue():
        if tool["name"] == name:
            return tool["input_schema"]
    raise AssertionError(f"{name} not advertised by build_tool_catalogue()")


@pytest.mark.parametrize("name", RECONCILED_TOOLS)
def test_manifest_params_subset_of_stager_accepted(name: str) -> None:
    """Every manifest-published param name is one the tool's stager accepts."""
    schema = _catalogue_schema(name)
    published = set(schema.get("properties", {}))
    accepted = STAGER_ACCEPTED_PARAMS[name]
    extra = published - accepted
    assert not extra, (
        f"{name}: manifest publishes param(s) the stager does not accept: "
        f"{sorted(extra)} (stager accepts: {sorted(accepted)})"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", RECONCILED_TOOLS)
async def test_stager_accepts_full_published_param_surface(name: str) -> None:
    """Calling the stager with the full advertised param surface stages cleanly.

    Builds an args dict from EVERY manifest-published param (sample values) and
    runs the bound stager. It must return a staged-change dict (``kind`` +
    ``payload``) without raising — i.e. an agent calling exactly per the
    published schema is staged, never fail-closed. Every required param must
    have a sample (no silent gap).
    """
    schema = _catalogue_schema(name)
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    missing_samples = required - set(_SAMPLE_VALUES)
    assert not missing_samples, (
        f"{name}: add a _SAMPLE_VALUES entry for required param(s) "
        f"{sorted(missing_samples)}"
    )

    args = {p: _SAMPLE_VALUES[p] for p in properties if p in _SAMPLE_VALUES}
    # Required params are all present (coverage asserted above).
    assert required <= set(args), (sorted(required), sorted(args))

    binding = TOOL_BINDINGS[name]
    assert binding.stager is not None, f"{name}: no stager bound"

    # Stagers may be sync (pure data-mappers) or async (those that resolve a
    # human-facing container label / summary asynchronously, e.g.
    # _stage_modify_profile). Dispatch awaits awaitable stager results
    # (dispatch.py: ``if inspect.isawaitable(staged): staged = await staged``),
    # so both shapes are valid — mirror that here.
    staged = binding.stager(args)
    if inspect.isawaitable(staged):
        staged = await staged
    assert isinstance(staged, dict), staged
    assert staged.get("kind"), staged
    assert isinstance(staged.get("payload"), dict), staged
    # The staged payload carries no identity/scope key (PC-1 / PC-2): the stager
    # maps data only — identity is the dispatch principal, scope is bound.
    assert "user_id" not in staged["payload"], staged["payload"]
    assert "workspace_id" not in staged["payload"], staged["payload"]
