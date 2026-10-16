"""Core-owned failures whose original receipts prove no mutation occurred.

This is deliberately an audited contract, not a classification of arbitrary
error names. Connector/App errors and unknown or partial outcomes never qualify.
Keep this list aligned with the pre-commit validation boundary in scaffold_build.
"""

BUILD_PRE_EFFECT_ERRORS = frozenset(
    {
        "invalid_scaffold_plan",
        "plan_differs_from_design",
        "scaffold_plan_validation_failed",
        "incomplete_scaffold",
    }
)


def is_pre_effect_rejection(receipt) -> bool:
    """Recognize an original Core validation receipt, including older runs."""
    return (
        getattr(receipt, "capability_source", None) == "core"
        and getattr(receipt, "capability_key", None) == "integral_build_approved_design"
        and getattr(receipt, "status", None) == "failed"
        and getattr(receipt, "adapter_error_code", None) in BUILD_PRE_EFFECT_ERRORS
    )


CORE_PRE_DISPATCH_REFUSALS = {
    "integral_build_approved_design": frozenset({"build_already_attempted"}),
    "integral_propose_design": frozenset({"approved_design_immutable"}),
}


def settled_core_guard_calls(history, effects) -> set[tuple[str, str]]:
    """Read original authenticated host returns, never model prose or new authority.

    These exact Core wrapper refusals return before broker dispatch. Require a
    matching original call and a completed library effect, not merely an error
    name in a failed/unresolved tool effect. Missing checkpoints stay ambiguous.
    """
    from app.agentive.harness.pydantic_ai_compat import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
    )

    latest = {}
    for effect in effects:
        latest[(effect.tool_call_id, effect.tool_name)] = effect.status
    calls = set()
    refused = set()
    for message in history:
        for part in message.parts:
            pair = (
                getattr(part, "tool_call_id", None),
                getattr(part, "tool_name", None),
            )
            if isinstance(message, ModelResponse) and isinstance(part, ToolCallPart):
                calls.add(pair)
            elif isinstance(message, ModelRequest) and isinstance(part, ToolReturnPart):
                data = part.content
                if (
                    pair in calls
                    and latest.get(pair) == "completed"
                    and isinstance(data, dict)
                    and data.get("error") is True
                    and data.get("retryable") is False
                    and data.get("error_code")
                    in CORE_PRE_DISPATCH_REFUSALS.get(part.tool_name, ())
                ):
                    refused.add(pair)
    return refused


def is_settled_core_receipt(receipt) -> bool:
    """Original Core outcomes can repair a lost conversational tool return."""
    return is_pre_effect_rejection(receipt) or (
        getattr(receipt, "capability_source", None) == "core"
        and getattr(receipt, "status", None) in {"succeeded", "denied"}
    )


def receipt_recovery_result(receipt) -> dict:
    """Outcome observation only; this grants no new execution authority."""
    if is_pre_effect_rejection(receipt):
        return {
            "error": True,
            "error_code": receipt.adapter_error_code,
            "applied": False,
            "message": "Core rejected this plan before mutation. It was not replayed.",
        }
    return {
        "error": receipt.status != "succeeded",
        "recorded_status": receipt.status,
        "observation_only": True,
        "message": (
            "The original Core receipt recorded this operation as "
            + receipt.status
            + ". This is a past outcome, not current state. Read current records "
            "before reporting or proposing further changes. Do not replay this operation."
        ),
    }
