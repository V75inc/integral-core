"""Stable, actionable browser messages for failed assistant turns."""

from types import SimpleNamespace

from app.api.errors import ResourceConflictError
from app.services.chat_streaming import classify_turn_exception


def test_unsettled_harness_run_explains_safe_recovery() -> None:
    exc = ResourceConflictError(
        message="The prior Harness run has an unsettled model request",
        details={"reason": "harness_model_request_unsettled", "request_count": 1},
    )

    code, message = classify_turn_exception(exc)

    assert code == "harness_reconciliation_required"
    assert "needs review" in message
    assert "not replayed" in message


def test_model_context_limit_is_actionable_without_leaking_provider_error() -> None:
    provider = SimpleNamespace(classify_exception=lambda _exc: "model_context_limit")
    code, message = classify_turn_exception(
        Exception("private provider detail"), provider=provider
    )

    assert code == "model_context_limit"
    assert "Shorten the request" in message
    assert "provider default" not in message


def test_harness_usage_limit_has_an_actionable_message() -> None:
    provider = SimpleNamespace(classify_exception=lambda _exc: "harness_usage_limit")
    code, message = classify_turn_exception(
        Exception("private usage details"), provider=provider
    )

    assert code == "harness_usage_limit"
    assert "safe generation limit" in message
    assert "80000" not in message


def test_exhausted_scaffold_retry_shows_seed_reason() -> None:
    """A spent build retry shows the seed reason, not a generic platform error."""
    cause = RuntimeError(
        "Operation 8: Seed line 'Berghotel Grosse Scheidegg' does not match "
        "a declared field. Correct the generated operation plan to match the "
        "saved, approved design and retry in this turn. Do not ask the user "
        "to repeat approval."
    )
    exc = RuntimeError(
        "Tool 'integral_build_approved_design' exceeded max retries count of 2."
    )
    exc.__cause__ = cause

    code, message = classify_turn_exception(exc)

    assert code == "scaffold_plan_invalid"
    assert "Berghotel Grosse Scheidegg" in message
    assert "does not match a declared field" in message
    assert "Correct the generated operation plan" not in message
    assert "Something went wrong on our side" not in message


def test_unrelated_exception_stays_internal_error() -> None:
    """Unrelated turn failures keep the generic internal-error sentence."""
    code, message = classify_turn_exception(RuntimeError("database unavailable"))
    assert code == "internal_error"
    assert message == "Something went wrong on our side. Please try again."


def test_first_conversation_state_conflict_is_not_described_as_prior_work(
    caplog,
) -> None:
    exc = ResourceConflictError(
        message="private exception text",
        details={
            "reason": "harness_checkpoint_manifest_unavailable",
            "private": "do not log",
        },
    )
    code, message = classify_turn_exception(exc)
    assert code == "harness_reconciliation_required"
    assert "could not safely complete" in message
    assert "earlier" not in message
    assert "harness_checkpoint_manifest_unavailable" in caplog.text
    assert "private" not in caplog.text
    assert "do not log" not in caplog.text


def test_unrecognized_harness_conflict_logs_only_fixed_fallback(caplog) -> None:
    exc = ResourceConflictError(
        message="private exception text",
        details={"reason": "harness_private_identifier_do_not_log"},
    )
    classify_turn_exception(exc)
    assert "harness_unclassified_conflict" in caplog.text
    assert "private" not in caplog.text
