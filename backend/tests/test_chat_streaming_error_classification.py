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
