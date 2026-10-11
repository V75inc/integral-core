"""Review boundary for agent-origin typed App commands; reads stay immediate."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from app.services.app_operations.dispatch import (
    invoke_app_operation,
    prepare_app_operation,
)


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
        "diff_machine": {"op": "invoke_app_operation", **payload},
        "payload": payload,
    }
