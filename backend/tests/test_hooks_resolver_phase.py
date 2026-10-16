"""Hook resolver phase defaults — after-save bindings must not match before_write."""

from __future__ import annotations

from app.services.hooks.resolver import _matches


def test_binding_without_phase_matches_after_write_only() -> None:
    match_block = {"entry_type": "candidate"}
    assert _matches(
        match_block,
        {"entry_type": "candidate", "phase": "after_write"},
    )
    assert not _matches(
        match_block,
        {"entry_type": "candidate", "phase": "before_write"},
    )


def test_binding_with_explicit_before_write_phase() -> None:
    match_block = {"entry_type": "candidate", "phase": "before_write"}
    assert _matches(
        match_block,
        {"entry_type": "candidate", "phase": "before_write", "write_actor": "public"},
    )
    assert not _matches(
        match_block,
        {"entry_type": "candidate", "phase": "after_write"},
    )


def test_payload_without_phase_keeps_wildcard_phase() -> None:
    match_block = {"entry_type": "candidate"}
    assert _matches(match_block, {"entry_type": "candidate"})
