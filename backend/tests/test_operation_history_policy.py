"""Own-history is a named observation permission, not a wildcard side effect."""

import pytest

from app.services.policy_engine import _action_matches, _is_write_action

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "granted", [["*"], ["operation.*"], ["workspace.read"], ["app.read"], []]
)
def test_legacy_grants_do_not_imply_own_history(granted):
    assert not _action_matches(granted, "operation.history.read_own")


def test_exact_history_permission_is_read_only():
    assert _action_matches(["operation.history.read_own"], "operation.history.read_own")
    assert not _is_write_action("operation.history.read_own")
    assert _is_write_action("operation.future_unknown")


def test_other_wildcard_contracts_are_preserved():
    assert _action_matches(["*"], "entry.create")
    assert _action_matches(["entry.*"], "entry.read")
    assert not _action_matches(["entry.*"], "track.read")
