"""Review boundary for agent-origin typed App commands; reads stay immediate."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from app.services.app_operations.dispatch import (
    invoke_app_operation,
    prepare_app_operation,
)


def _input_review_labels(
    schema: Any, *, depth: int = 0, remaining: list[int] | None = None
) -> Dict[str, Any]:
    """Small presentation-only schema projection, never execution authority.

    App-owned titles supply domain vocabulary. Unsupported schemas simply use
    literal field keys; the complete validated input remains in diff_machine.
    """
    if remaining is None:
        remaining = [256]
    remaining[0] -= 1
    if not isinstance(schema, dict) or depth > 8 or remaining[0] < 0:
        return {}
    labels: Dict[str, Any] = {}
    title = schema.get("title")
    if isinstance(title, str) and title.strip():
        labels["title"] = title[:160]
    properties = schema.get("properties")
    if isinstance(properties, dict):
        projected = {
            key: child
            for key, spec in list(properties.items())[:256]
            if (
                child := _input_review_labels(
                    spec, depth=depth + 1, remaining=remaining
                )
            )
        }
        if projected:
            labels["properties"] = projected
        # Object-member order is not durable in JSONB. Authors may declare a
        # presentation array; transport/storage preserve its sequence.
        order = schema.get("x-review-order")
        if isinstance(order, list):
            seen: set[str] = set()
            ordered = []
            for key in order[:256]:
                if isinstance(key, str) and key in properties and key not in seen:
                    seen.add(key)
                    ordered.append(key)
            if ordered:
                labels["property_order"] = ordered
    items = _input_review_labels(
        schema.get("items"), depth=depth + 1, remaining=remaining
    )
    if items:
        labels["items"] = items
    return labels


async def prepare_agent_app_operation(
    args: Dict[str, Any], *, principal_id: str, workspace_id: str
) -> Dict[str, Any]:
    """Validate a read or prepare a bound command without running its handler."""
    app_id = str(args.get("app_id") or "").strip()
    key = str(args.get("operation_key") or "").strip()
    body = args.get("input", {})
    if not app_id or not key or not isinstance(body, dict):
        raise ValueError("app_id, operation_key and an object input are required")
    prepared = await prepare_app_operation(
        user_id=principal_id,
        workspace_id=workspace_id,
        app_id=app_id,
        operation_key=key,
        payload=body,
    )
    if prepared["operation_kind"] == "read":
        output = await invoke_app_operation(
            user_id=principal_id,
            workspace_id=workspace_id,
            app_id=app_id,
            operation_key=key,
            payload=prepared["body"],
            expected_declaration_fingerprint=prepared["declaration_fingerprint"],
            expected_operation_kind="read",
        )
        return {"_no_stage": True, "data": output}

    spec, body = prepared["spec"], prepared["body"]
    declaration = prepared["declaration_fingerprint"]
    # Namespace the proposal identity: the same caller key for another command
    # or another input must never return an unrelated existing approval card.
    proposal_key = hashlib.sha256(
        json.dumps(
            [
                principal_id,
                workspace_id,
                app_id,
                key,
                body,
                declaration,
                str(args.get("idempotency_key") or ""),
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    label = str(spec.get("name") or key)
    payload = {
        "app_id": app_id,
        "operation_key": key,
        "input": body,
        "idempotency_key": "app-review:" + proposal_key,
        "operation_idempotency_key": str(args.get("idempotency_key") or "")
        or "app-review:" + proposal_key,
        "declaration_fingerprint": declaration,
        "correlation_id": str(args.get("correlation_id") or ""),
    }
    return {
        "kind": "invoke_app_operation",
        "summary": f"Run “{label}”",
        "diff_human": (
            f"**{label}**\n\n{spec.get('description') or ''}\n\n"
            "Nothing has been applied. Approval runs the following inputs:\n\n```json\n"
            + json.dumps(body, ensure_ascii=False, indent=2)
            + "\n```"
        ),
        "diff_machine": {
            "op": "invoke_app_operation",
            **payload,
            "review": {
                "description": str(spec.get("description") or ""),
                "input_labels": _input_review_labels(spec.get("input_schema")),
            },
        },
        "payload": payload,
    }
