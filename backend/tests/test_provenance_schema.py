"""Provenance Pydantic model + ActorKind Literal regression tests (Plan 02-01 Task 1).

Verifies the contract for the shared who-is-the-actor axis (D-01, D-10).
"""

from datetime import timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from app.schemas.provenance import ActorKind, Provenance


def test_actor_kind_has_four_canonical_members():
    """D-10: ActorKind Literal MUST be exactly {human, agent, connector, system}."""
    assert set(get_args(ActorKind)) == {"human", "agent", "connector", "system"}


def test_provenance_default_for_human_source():
    """D-01: a Provenance constructed with only source='human' gets canonical defaults."""
    p = Provenance(source="human")
    assert p.source == "human"
    assert p.source_id is None
    assert p.confidence == 1.0
    assert p.derived_from == []
    assert p.synced_at.tzinfo is not None  # tz-aware
    # tz is UTC (utc_now() always returns UTC)
    assert p.synced_at.utcoffset() == timezone.utc.utcoffset(p.synced_at)


def test_provenance_rejects_confidence_above_one():
    """D-01: confidence is bounded [0, 1] — 1.5 must 422."""
    with pytest.raises(ValidationError):
        Provenance(source="human", confidence=1.5)


def test_provenance_rejects_negative_confidence():
    """D-01: confidence is bounded [0, 1] — -0.1 must 422."""
    with pytest.raises(ValidationError):
        Provenance(source="human", confidence=-0.1)


def test_provenance_rejects_invalid_actor_kind():
    """D-10: ActorKind Literal rejects non-member strings."""
    with pytest.raises(ValidationError):
        Provenance(source="invalid_kind")  # type: ignore[arg-type]


def test_provenance_rejects_extra_fields():
    """D-01: extra=forbid — unknown keys raise ValidationError (422 via canonical envelope)."""
    with pytest.raises(ValidationError):
        Provenance(source="human", unknown_field="x")  # type: ignore[call-arg]


def test_human_default_returns_canonical_human_provenance():
    """``Provenance.human_default()`` is the standard default at Entry create."""
    p = Provenance.human_default()
    assert p.source == "human"
    assert p.confidence == 1.0
    assert p.derived_from == []
    assert p.synced_at.tzinfo is not None  # tz-aware utc_now


def test_provenance_accepts_all_actor_kinds():
    """D-10: all four ActorKind members construct a valid Provenance."""
    for kind in ("human", "agent", "connector", "system"):
        p = Provenance(source=kind)  # type: ignore[arg-type]
        assert p.source == kind


def test_provenance_round_trip_via_model_dump_and_validate():
    """Round-trip: dump and re-validate preserves all fields (matters for jvspatial persistence)."""
    original = Provenance(
        source="agent",
        source_id="agent-config-abc",
        confidence=0.75,
        derived_from=["entry-x", "entry-y"],
    )
    dumped = original.model_dump(mode="json")
    rebuilt = Provenance.model_validate(dumped)
    assert rebuilt.source == "agent"
    assert rebuilt.source_id == "agent-config-abc"
    assert rebuilt.confidence == 0.75
    assert rebuilt.derived_from == ["entry-x", "entry-y"]
    assert rebuilt.synced_at == original.synced_at
