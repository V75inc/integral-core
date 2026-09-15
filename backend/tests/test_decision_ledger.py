"""The decision ledger — ADR-007.

Every approval card a person acts on is a decision about their own work.
``staging_store`` keeps only ``pending`` and ``blessed`` and drops terminal
rows on transition — correct for a durability mirror, and the reason the
record has to be taken at the moment of transition, before the row goes.

What is defended here:

- bless, reject and expire each leave a ``Decision``, with the outcome, the
  diff the person was actually shown, and the token it came from;
- **expiry leaves a trace at all** — before this it left none anywhere: the
  lazy-expiry path raises before any push and the sweeper never pushed;
- a ledger failure never fails an approval;
- a person without the App gets no write and no error;
- the ledger lands unstaged, because recording that somebody approved
  something cannot itself require approval;
- a ``Decision`` is a record of an event, not a belief: it carries no
  belief fields and lives in its own track.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List
from unittest.mock import patch

import pytest
import yaml

# Module scope on purpose — these load .env as an import side effect, and
# left to a lazy import inside a test body that mutation trips conftest's
# env-leak guard at teardown.
import app.api.entries  # noqa: F401
from app.agentive import decision_ledger, staging
from app.models.edges import CONTAINS
from app.models.nodes import Entry
from app.services.personal_context import provision_personal_context_app

pytestmark = pytest.mark.library

_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "profiles"
    / "personal-context"
    / "profile.yaml"
)


async def _tracks(user_id: str) -> dict:
    app_node = await provision_personal_context_app(user_id=user_id)
    assert app_node is not None
    return {
        str(getattr(t, "template_id", "") or ""): t
        for t in await app_node.nodes(edge=[CONTAINS], node=["Track"], direction="out")
    }


async def _ledger_rows(user_id: str) -> List[Any]:
    track = (await _tracks(user_id))["decisions"]
    return list((await Entry.find({"context.track_id": track.id})) or [])


async def _stage(user_id: str, *, summary: str = "Create entry “Note”") -> Any:
    return await staging.create_staged_change(
        user_id=user_id,
        session_id=None,
        kind="create_entry",
        summary=summary,
        diff_human=f"**{summary}**\n\n- **Track:** Notes",
        diff_machine={"op": "create_entry"},
        payload={"track_id": "n.Track.x", "title": "Note"},
    )


# ---------------------------------------------------------------------------
# The model: a decision is not a belief
# ---------------------------------------------------------------------------


def test_decisions_live_in_their_own_track():
    # Gate B's ruling. Decision shared a track with Commitment, which is a
    # belief and must keep staging — so the ledger's exemption would have
    # dragged beliefs into it, the staging exemption being a whole-track
    # property (I-PC-01).
    manifest = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    tracks = {t["key"]: t for t in manifest["app"]["tracks"]}

    assert "decisions" in tracks
    assert [e["key"] for e in tracks["decisions"]["entry_types"]] == ["decision"]
    assert "decision" not in [
        e["key"] for e in tracks["commitments"]["entry_types"]
    ], "Decision is back in the commitments track"

    assert manifest["app"]["unstaged_tracks"] == ["stream", "attention", "decisions"]


def test_a_decision_carries_no_belief_fields():
    # It records an event that happened, not a claim that could turn out to
    # be wrong. Nothing supersedes it and nothing scores its confidence.
    manifest = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    tracks = {t["key"]: t for t in manifest["app"]["tracks"]}
    fields = {f["key"] for f in tracks["decisions"]["entry_types"][0]["fields"]}
    for belief_field in (
        "confidence",
        "status",
        "first_seen",
        "last_confirmed",
        "superseded_by",
        "sources",
    ):
        assert belief_field not in fields, f"Decision carries {belief_field}"


def test_edited_then_blessed_is_declared_but_unmapped():
    # There is no edit-before-bless path in the codebase: bless_token takes
    # (user_id, token, autonomy) and flips state. The enum member is
    # RESERVED — removing it later is a manifest edit, adding it back is a
    # migration — and this test is the record of why nothing writes it.
    manifest = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    tracks = {t["key"]: t for t in manifest["app"]["tracks"]}
    outcome = next(
        f
        for f in tracks["decisions"]["entry_types"][0]["fields"]
        if f["key"] == "outcome"
    )
    assert "edited_then_blessed" in outcome["enum"]
    assert "edited_then_blessed" not in decision_ledger._OUTCOME_BY_STATE.values()

    src = (
        Path(__file__).resolve().parents[1] / "app" / "agentive" / "staging.py"
    ).read_text(encoding="utf-8")
    assert "async def bless_token(" in src
    assert (
        "payload=" not in src.split("async def bless_token(")[1][:400]
    ), "bless_token now accepts a payload — wire edited_then_blessed"


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_blessed_and_consumed_card_is_recorded(test_user):
    principal = str(getattr(test_user, "user_id", "") or test_user.id)
    await _tracks(test_user.id)
    before = len(await _ledger_rows(test_user.id))

    sc = await _stage(principal, summary="Create entry “Deck notes”")
    await staging.bless_token(user_id=principal, token=sc.token)
    await staging.consume_token(
        user_id=principal, token=sc.token, expected_kind="create_entry"
    )

    rows = await _ledger_rows(test_user.id)
    assert len(rows) == before + 1
    row = rows[-1]
    fields = dict(getattr(row, "custom_fields", None) or {})
    assert fields.get("outcome") == "blessed"
    assert fields.get("proposal_kind") == "create_entry"
    assert fields.get("token_ref") == sc.token
    assert fields.get("decided_at")
    # The diff the person was SHOWN, not the machine payload.
    assert "Deck notes" in str(fields.get("diff") or "")


@pytest.mark.asyncio
async def test_a_rejected_card_is_recorded(test_user):
    principal = str(getattr(test_user, "user_id", "") or test_user.id)
    await _tracks(test_user.id)
    before = len(await _ledger_rows(test_user.id))

    sc = await _stage(principal, summary="Create entry “Rejected thing”")
    await staging.revoke_token(user_id=principal, token=sc.token)

    rows = await _ledger_rows(test_user.id)
    assert len(rows) == before + 1
    assert (
        dict(getattr(rows[-1], "custom_fields", None) or {}).get("outcome")
        == "rejected"
    )


@pytest.mark.asyncio
async def test_an_expired_card_leaves_a_trace(test_user):
    """The path that produced nothing at all before ADR-007.

    ``consume_token`` raises ``StagingError("expired")`` before any push, and
    ``_sweep_expired_locked`` never pushed — so an expired card left no WS
    event, no closure marker, and no transcript rewrite. "What I let expire"
    was unrecordable.
    """
    from datetime import datetime, timedelta, timezone

    principal = str(getattr(test_user, "user_id", "") or test_user.id)
    await _tracks(test_user.id)
    before = len(await _ledger_rows(test_user.id))

    sc = await _stage(principal, summary="Create entry “Ignored thing”")
    # Age it past its TTL rather than waiting for one.
    sc.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)

    with pytest.raises(staging.StagingError):
        await staging.consume_token(
            user_id=principal, token=sc.token, expected_kind="create_entry"
        )

    rows = await _ledger_rows(test_user.id)
    assert len(rows) == before + 1, "an expired card left no trace"
    assert (
        dict(getattr(rows[-1], "custom_fields", None) or {}).get("outcome") == "expired"
    )


@pytest.mark.asyncio
async def test_an_expiry_is_recorded_once(test_user):
    from datetime import datetime, timedelta, timezone

    principal = str(getattr(test_user, "user_id", "") or test_user.id)
    await _tracks(test_user.id)
    before = len(await _ledger_rows(test_user.id))

    sc = await _stage(principal, summary="Create entry “Ignored twice”")
    sc.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)

    for _ in range(3):
        with pytest.raises(staging.StagingError):
            await staging.consume_token(
                user_id=principal, token=sc.token, expected_kind="create_entry"
            )

    assert len(await _ledger_rows(test_user.id)) == before + 1


# ---------------------------------------------------------------------------
# Failure policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_ledger_failure_never_fails_an_approval(test_user):
    principal = str(getattr(test_user, "user_id", "") or test_user.id)
    await _tracks(test_user.id)

    sc = await _stage(principal)
    await staging.bless_token(user_id=principal, token=sc.token)

    with patch.object(
        decision_ledger, "_record", side_effect=RuntimeError("ledger is down")
    ):
        payload = await staging.consume_token(
            user_id=principal, token=sc.token, expected_kind="create_entry"
        )

    assert payload["title"] == "Note", "the approval failed because the ledger did"
    assert (await staging.get_token(sc.token)).state == "consumed"


@pytest.mark.asyncio
async def test_a_person_without_the_app_gets_no_write_and_no_error(test_user):
    principal = str(getattr(test_user, "user_id", "") or test_user.id)
    sc = await _stage(principal)
    await staging.bless_token(user_id=principal, token=sc.token)

    with patch(
        "app.services.personal_context.resolve_personal_context", return_value=None
    ):
        payload = await staging.consume_token(
            user_id=principal, token=sc.token, expected_kind="create_entry"
        )
    assert payload["title"] == "Note"


@pytest.mark.asyncio
async def test_the_ledger_lands_unstaged(test_user):
    # Recording that somebody approved something cannot itself require
    # approval. If this regresses, a person blessing one card gets a second
    # card asking them to approve the record of the first.
    principal = str(getattr(test_user, "user_id", "") or test_user.id)
    await _tracks(test_user.id)

    sc = await _stage(principal)
    await staging.bless_token(user_id=principal, token=sc.token)
    await staging.consume_token(
        user_id=principal, token=sc.token, expected_kind="create_entry"
    )

    pending = await staging.get_pending_for_user(principal)
    kinds = [p.kind for p in pending or []]
    assert not kinds, f"the ledger write minted approval cards: {kinds}"


# ---------------------------------------------------------------------------
# Substrate boundary
# ---------------------------------------------------------------------------


def test_the_ledger_names_no_bundle():
    src = (
        Path(__file__).resolve().parents[1] / "app" / "agentive" / "decision_ledger.py"
    ).read_text(encoding="utf-8")
    for token in ("personal-context", "PersonalContext"):
        assert token not in src, f"the ledger names the bundle: {token}"
    # It reaches the substrate the same way every other agent write does.
    assert "from app.agentive.tooling import dispatch_tool" in src
    assert "integral_create_entry" in src
    for forbidden in ("Entry.create(", ".save()"):
        assert forbidden not in src, f"the ledger bypasses the seam: {forbidden}"


def test_the_record_is_taken_before_the_row_is_dropped():
    src = (
        Path(__file__).resolve().parents[1] / "app" / "agentive" / "staging.py"
    ).read_text(encoding="utf-8")
    terminal = src.split('if sc.state in ("consumed", "revoked"):')[1][:900]
    assert terminal.index("record_decision(sc)") < terminal.index(
        "staging_store.remove"
    ), "the durable row is dropped before the decision is recorded"
