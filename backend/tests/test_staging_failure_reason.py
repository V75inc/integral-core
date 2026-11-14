"""Why an approved write did not land, recorded where every surface can see it.

The executor's refusal used to live only in the HTTP response to whoever
clicked Approve. Observed: approving from the agent inbox left the chat card
showing "approved" with no reason, because that card is a different hook
instance and only ever learned the state.

Every surface now says "approved — not yet applied" honestly; this is what
lets them also say *why*.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.agentive import staging
from app.agentive.staging import StagedChange, record_execute_outcome


def _staged(token: str = "tok-fail") -> StagedChange:
    now = datetime.now(timezone.utc)
    return StagedChange(
        token=token,
        user_id="u_1",
        session_id="sess_1",
        kind="create_track",
        summary="Create track “Field Marketing”",
        diff_human="",
        diff_machine={},
        payload={},
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        state="blessed",
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    staging._reset_for_tests()
    yield
    staging._reset_for_tests()


@pytest.mark.asyncio
async def test_a_refusal_is_recorded_on_the_change():
    sc = _staged()
    staging._tokens[sc.token] = sc

    out = await record_execute_outcome(
        token=sc.token,
        error={"message": "Cannot add a track to this app", "error_code": "403"},
    )

    assert out is not None
    assert out.last_error is not None
    assert out.last_error["message"] == "Cannot add a track to this app"


@pytest.mark.asyncio
async def test_the_reason_rides_along_with_the_state():
    # `to_dict` is what the WS push, /pending and /token/{id} all serialize —
    # putting it there is what makes the reason reach a surface that did not
    # click.
    sc = _staged()
    staging._tokens[sc.token] = sc
    await record_execute_outcome(token=sc.token, error={"message": "refused"})

    payload = staging._tokens[sc.token].to_dict()
    assert payload["last_error"]["message"] == "refused"


@pytest.mark.asyncio
async def test_a_later_success_clears_the_explanation():
    # Otherwise a retry that works leaves a stale reason under a change that
    # actually applied.
    sc = _staged()
    staging._tokens[sc.token] = sc
    await record_execute_outcome(token=sc.token, error={"message": "refused"})

    await record_execute_outcome(token=sc.token, error=None)

    assert staging._tokens[sc.token].last_error is None


@pytest.mark.asyncio
async def test_an_unknown_token_is_not_an_error():
    # Recording is best-effort bookkeeping around a write that already
    # happened; it must never become a second failure.
    assert await record_execute_outcome(token="nope", error={"message": "x"}) is None


@pytest.mark.asyncio
async def test_a_clean_change_carries_no_reason():
    sc = _staged()
    staging._tokens[sc.token] = sc

    assert sc.to_dict()["last_error"] is None


@pytest.mark.parametrize(
    "completed,child_state,expected",
    [
        (0, None, "not_applied"),
        (1, None, "partially_applied"),
        (0, "partially_applied", "partially_applied"),
        (1, "not_applied", "partially_applied"),
        (0, "reconciliation_required", "reconciliation_required"),
        (1, "reconciliation_required", "reconciliation_required"),
    ],
)
def test_durable_batch_refusal_preserves_reason_and_honest_application_state(
    completed, child_state, expected
):
    from app.agentive.services.staging_command_batch import _batch_step_failure

    child = {
        "error": True,
        "error_code": "bad_request",
        "status_code": 400,
        "message": "The required status field is missing.",
    }
    if child_state:
        child["application_state"] = child_state
    results = [{"kind": "create_entry", "result": child}]
    error = _batch_step_failure(index=completed, total=2, result=child, results=results)
    assert child["message"] in error["message"]
    assert error["completed"] == completed
    assert error["status_code"] == 400
    assert error["error_code"] == "bad_request"
    assert error["application_state"] == expected
    assert error["results"] is results


@pytest.mark.asyncio
async def test_durable_batch_child_reason_reaches_saved_parent_error():
    from app.agentive.services.staging_command_batch import _batch_step_failure

    sc = _staged()
    staging._tokens[sc.token] = sc
    error = _batch_step_failure(
        index=0,
        total=2,
        result={"error": True, "message": "Revise the body."},
        results=[],
    )
    await record_execute_outcome(token=sc.token, error=error)
    assert (
        "Revise the body."
        in staging._tokens[sc.token].to_dict()["last_error"]["message"]
    )
    assert staging._tokens[sc.token].last_error["application_state"] == "not_applied"
