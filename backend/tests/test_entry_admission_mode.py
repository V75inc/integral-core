"""Which Entry writes are admitted under shared locks, and which stay exclusive."""

import pytest

from app.services.entry_effect_scope import admission_is_shared


def _mode(monkeypatch, enabled, **kw):
    from app.config import settings

    monkeypatch.setattr(settings, "ENTRY_SHARED_ADMISSION_ENABLED", enabled)
    base = dict(
        action="entry.create",
        target_kind="track",
        payload={},
        subject_kind="human",
        shared_create=False,
    )
    return admission_is_shared(**{**base, **kw})


def test_metadata_only_updates_and_guarded_creates_always_share(monkeypatch):
    for enabled in (False, True):
        assert _mode(
            monkeypatch,
            enabled,
            action="entry.update",
            target_kind="entry",
            payload={"title": "x"},
        )
        assert _mode(monkeypatch, enabled, shared_create=True)


def test_a_person_creating_a_record_is_exclusive_until_the_setting_is_on(monkeypatch):
    assert _mode(monkeypatch, False) is False
    assert _mode(monkeypatch, True) is True


def test_field_updates_share_only_when_the_setting_is_on(monkeypatch):
    update = dict(
        action="entry.update", target_kind="entry", payload={"custom_fields": {"a": 1}}
    )
    assert _mode(monkeypatch, False, **update) is False
    assert _mode(monkeypatch, True, **update) is True


@pytest.mark.parametrize("enabled", [False, True])
def test_type_changes_and_non_human_actors_stay_exclusive(monkeypatch, enabled):
    assert not _mode(
        monkeypatch,
        enabled,
        action="entry.update",
        target_kind="entry",
        payload={"type_id": "t", "custom_fields": {"a": 1}},
    )
    assert not _mode(monkeypatch, enabled, subject_kind="agent")
    assert not _mode(
        monkeypatch, enabled, subject_kind="connector", shared_create=False
    )
    assert not _mode(
        monkeypatch,
        enabled,
        action="entry.update",
        target_kind="entry",
        payload={"custom_fields": {"a": 1}},
        subject_kind="agent",
    )


def test_other_actions_are_never_shared(monkeypatch):
    for action, kind in (
        ("entry.delete", "entry"),
        ("entry.create", "entry"),
        ("entry.update", "track"),
    ):
        assert _mode(monkeypatch, True, action=action, target_kind=kind) is False
