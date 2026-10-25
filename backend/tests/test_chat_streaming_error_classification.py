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
    assert "in this chat" in message
    assert "Start a new conversation" not in message
    assert "haven't been repeated" in message


def test_model_context_limit_is_actionable_without_leaking_provider_error() -> None:
    provider = SimpleNamespace(classify_exception=lambda _exc: "model_context_limit")
    code, message = classify_turn_exception(
        Exception("private provider detail"), provider=provider
    )

    assert code == "model_context_limit"
    assert "larger context window" in message
    assert "provider default" not in message


def test_host_context_limit_is_distinct_from_provider_overflow() -> None:
    provider = SimpleNamespace(classify_exception=lambda _exc: "harness_context_limit")
    code, message = classify_turn_exception(
        Exception("private context dimensions"), provider=provider
    )
    assert code == "harness_context_limit"
    assert "conversation is saved" in message
    assert "start a new chat" not in message
    assert "private" not in message


def test_harness_usage_limit_has_an_actionable_message() -> None:
    provider = SimpleNamespace(classify_exception=lambda _exc: "harness_usage_limit")
    code, message = classify_turn_exception(
        Exception("private usage details"), provider=provider
    )

    assert code == "harness_usage_limit"
    assert "couldn't finish everything" in message
    assert "in this chat" in message
    assert "saved changes and approvals" in message
    assert "new conversation" not in message
    assert "80000" not in message


def test_exhausted_scaffold_retry_explains_problem_without_internal_seed_detail() -> (
    None
):
    """A spent build retry explains the next step without exposing internal input."""
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
    assert "Berghotel Grosse Scheidegg" not in message
    assert "details didn't fit together" in message
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
    assert "couldn't finish" in message
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


def test_invalid_operator_config_has_a_safe_setup_message():
    from app.api.errors import ServiceUnavailableError

    exc = ServiceUnavailableError(
        message="private-config",
        details={"reason": "native_model_configuration_invalid"},
    )
    code, message = classify_turn_exception(exc)
    assert code == "model_configuration_invalid"
    assert "Settings" in message
    assert "INTEGRAL_NATIVE_MODEL" not in message
    assert "private-config" not in message


def test_nontransactional_preview_explains_live_chat_storage_requirement():
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
    )

    code, message = classify_turn_exception(
        OperationTransactionUnavailable(
            "Harness session activation requires a transactional shared store"
        )
    )
    assert code == "chat_storage_unavailable"
    assert "workspace administrator" in message
    assert "try again" not in message.lower()


def test_nested_rollback_does_not_claim_postgres_is_missing():
    from app.services.app_operations.transaction_scope import (
        OperationTransactionRolledBack,
    )

    code, message = classify_turn_exception(
        OperationTransactionRolledBack("private failure")
    )
    assert code == "chat_step_rolled_back"
    assert "changes weren't saved" in message
    assert "PostgreSQL" not in message
    assert "private failure" not in message


def test_all_host_failure_messages_use_everyday_language():
    from app.services.chat_streaming import _ERROR_MESSAGES

    forbidden = (
        "INTEGRAL_",
        "OLLAMA_",
        "PostgreSQL",
        "tool use",
        "generation limit",
        "rolled back",
        "checkpoint",
        "receipts",
    )
    for code, message in _ERROR_MESSAGES.items():
        assert message, code
        assert not any(word in message for word in forbidden), code


def test_generic_tool_retry_exhaustion_does_not_claim_an_approved_build_failed():
    from pydantic_ai.exceptions import ModelRetry, UnexpectedModelBehavior

    from app.agentive.harness.pydantic_ai_compat import (
        classify_integral_harness_exception,
    )

    exc = UnexpectedModelBehavior(
        "Tool 'load_capability' exceeded max retries count of 1."
    )
    exc.__cause__ = ModelRetry("No capability found with id 'unknown-tool'.")
    provider = SimpleNamespace(classify_exception=classify_integral_harness_exception)
    code, message = classify_turn_exception(exc, provider=provider)
    assert code == "model_response_invalid"
    assert "approved" not in message.lower()
    assert "unknown-tool" not in message
