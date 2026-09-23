"""TEMPLATED_FROM edge tests — Phase 3.1 Plan 03.1-02 Task 1 (ANC-04).

Covers:
  - Class identity + ALL_CAPS alias (Phase 1 D-07 convention).
  - Locked schema fields (``template_key``, ``provisioned_at``, ``bidirectional``).
  - Distinctness from ``USES_TEMPLATE`` (the legacy Track → Track edge MUST NOT
    be overloaded — they coexist with different sources / targets / semantics).
  - Wire round-trip Track → OperationalModel.

Mirrors the test pattern from ``tests/test_anchor_edge.py`` (Plan 03.1-01).
Does NOT import ``app.main`` — the pre-existing
``app.middleware.test_auth`` import failure (see
``.planning/phases/03.1-anchor-associations-governance/deferred-items.md``)
would block any test that did. The substrate functions under test are
imported directly.
"""

from __future__ import annotations

import pytest

from app.models.edges import (
    TEMPLATED_FROM,
    USES_TEMPLATE,
    TemplatedFrom,
)
from app.models.nodes import OperationalModel, Track


def test_templated_from_alias_identity():
    """ALL_CAPS alias is the same class object (Phase 1 D-07)."""
    assert TEMPLATED_FROM is TemplatedFrom


def test_templated_from_default_field_values():
    """Locked schema: class-level defaults match the documented contract.

    Asserts ``model_fields`` rather than instance attributes because the
    jvspatial ``Edge`` base ``__init__`` overrides instance ``bidirectional``
    regardless of subclass declaration (same behavior as REFERENCES /
    ANCHORS / HAS_POLICY — verified in Plan 03.1-01).
    """
    fields = TemplatedFrom.model_fields
    assert fields["template_key"].default is None
    assert fields["provisioned_at"].default is None
    assert fields["bidirectional"].default is False


def test_templated_from_distinct_from_uses_template():
    """TEMPLATED_FROM must NOT be an alias of USES_TEMPLATE.

    They are conceptually different edges with different sources / targets:
      - USES_TEMPLATE  : Track → Track            (legacy seed provenance)
      - TEMPLATED_FROM : Track → OperationalModel   (Phase 3.1 ANC-04 lineage)
    """
    assert TEMPLATED_FROM is not USES_TEMPLATE
    assert TemplatedFrom is not USES_TEMPLATE


@pytest.mark.asyncio
async def test_templated_from_wire_round_trip():
    """Track → OperationalModel via TEMPLATED_FROM with template_key payload."""
    track = await Track.create(
        title="Anchored Track",
        owner_id="user-tf-1",
        workspace_id="ws-tf-1",
    )
    template_cp = await OperationalModel.create(
        name="Project Details",
        scope="track",
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "track": {
                "entry_types": [],
                "taxonomy": {"tag_groups": []},
                "views": [],
            },
        },
        library_package=False,
    )
    await track.connect(
        template_cp,
        edge=TEMPLATED_FROM,
        template_key="project-details",
        provisioned_at="2026-05-16T00:00:00+00:00",
    )

    # Out-direction traversal yields the CP.
    outs = await track.nodes(edge=[TEMPLATED_FROM], direction="out")
    assert len(outs) == 1
    assert outs[0].id == template_cp.id

    # In-direction traversal from CP yields the Track.
    ins = await template_cp.nodes(edge=[TEMPLATED_FROM], direction="in", node=["Track"])
    assert len(ins) == 1
    assert ins[0].id == track.id


@pytest.mark.asyncio
async def test_templated_from_many_tracks_one_template():
    """Multiple Tracks can share one template CP via TEMPLATED_FROM (by-reference)."""
    template_cp = await OperationalModel.create(
        name="Shared Template",
        scope="track",
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "track": {
                "entry_types": [],
                "taxonomy": {"tag_groups": []},
                "views": [],
            },
        },
        library_package=False,
    )
    t1 = await Track.create(title="T1", owner_id="user-tf-2", workspace_id="ws-tf-2")
    t2 = await Track.create(title="T2", owner_id="user-tf-2", workspace_id="ws-tf-2")
    await t1.connect(template_cp, edge=TEMPLATED_FROM, template_key="shared")
    await t2.connect(template_cp, edge=TEMPLATED_FROM, template_key="shared")

    ins = await template_cp.nodes(edge=[TEMPLATED_FROM], direction="in", node=["Track"])
    in_ids = {n.id for n in ins}
    assert t1.id in in_ids
    assert t2.id in in_ids
