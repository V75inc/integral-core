"""Explicit-source document filing as one reviewed canonical operation batch."""

from __future__ import annotations

import json
from typing import Any


async def stage_file_uploaded_document(args: dict[str, Any]) -> dict[str, Any]:
    """Prepare one explicit-source create/update/attach review without effects."""
    from app.agentive.batch_validation import validate_batch_references
    from app.agentive.staging import is_batch_open
    from app.agentive.tooling.bindings import (
        _bound_propose_principal,
        _bound_propose_session_id,
        _stage_attach_uploaded_file,
        _stage_create_entry,
        _stage_update_entry,
    )
    from app.services.agent_scope import active_workspace_id
    from app.services.attachment_agent import (
        get_attachment_text,
        validate_chat_attachment_source,
    )
    from app.services.chat_threads import get_thread_by_session

    principal = _bound_propose_principal()
    session = _bound_propose_session_id()
    if not principal or not session:
        raise ValueError("A current authenticated conversation is required.")
    if is_batch_open(principal, session):
        raise ValueError(
            "Finish or cancel the existing batch before filing a document."
        )
    mode = args.get("mode")
    if mode not in {"create", "update", "attach"}:
        raise ValueError("mode must be create, update or attach.")
    source_id = str(args.get("attachment_id") or "")
    if not source_id or len(source_id) > 256:
        raise ValueError("A real uploaded attachment_id is required.")
    if len(json.dumps(args, ensure_ascii=False)) > 100000:
        raise ValueError(
            "Document filing input is too large; use a concise factual summary."
        )
    thread = await get_thread_by_session(session)
    if thread is None:
        raise ValueError("The source conversation is unavailable.")
    _, _, error = await validate_chat_attachment_source(
        user_id=principal,
        attachment_id=source_id,
        thread_id=thread.id,
        workspace_id=active_workspace_id(),
    )
    if error:
        if error.get("error_code") == "not_chat_owned":
            raise ValueError(
                "The source has already been filed. Inspect the existing target's "
                "attachments and the previous filing receipt to reconcile the result; "
                "do not recreate the record or upload another copy. Nothing was staged."
            )
        raise ValueError(error["message"])
    # This is a content-derived filing, not a filename-derived placeholder.
    # Attach-only is deliberately available for a record already populated.
    if mode != "attach":
        source = await get_attachment_text(principal, source_id, max_chars=12000)
        if not source.get("text") or source.get("metadata_status") != "complete":
            raise ValueError(
                "The document text is not available. Read or prepare the source before filing its content."
            )

    operations = []
    prepared_create = None
    if mode == "create":
        if args.get("entry_id") or args.get("updates"):
            raise ValueError("Create mode cannot target an existing entry or updates.")
        if not all(
            isinstance(args.get(key), str) and args[key].strip()
            for key in ("track_id", "title", "text")
        ):
            raise ValueError(
                "Create requires an explicit track_id, title and source-derived text."
            )
        if args["track_id"].startswith("{{"):
            raise ValueError("Choose an existing track, not a pending batch reference.")
        prepared_create = await _stage_create_entry(
            {
                key: args[key]
                for key in ("track_id", "title", "text", "fields", "entry_type")
                if key in args
            }
        )
        operations.append(prepared_create)
        target = "{{step_1.id}}"
    else:
        target = str(args.get("entry_id") or "")
        if not target or target.startswith("{{") or len(target) > 256:
            raise ValueError(
                "An existing real entry_id is required; this mode never creates a record."
            )
        if any(
            key in args for key in ("track_id", "title", "text", "fields", "entry_type")
        ):
            raise ValueError(
                "Existing-entry modes use entry_id and updates, not create fields."
            )
        if mode == "update":
            updates = args.get("updates")
            if not isinstance(updates, dict) or not updates:
                raise ValueError("Update requires explicit updates.")
            operations.append(
                await _stage_update_entry({"entry_id": target, "updates": updates})
            )
        elif args.get("updates"):
            raise ValueError("Attach mode does not change record content.")
    operations.append(
        await _stage_attach_uploaded_file(
            {"entry_id": target, "attachment_id": source_id},
            prepared_create=prepared_create,
        )
    )
    validate_batch_references(operations)
    # These are plans only: no open batch, temporary card, token, or entry was
    # created while validating. Normal dispatcher mints exactly one review.
    return {
        "kind": "batch",
        "summary": f"File uploaded document ({mode})",
        "diff_human": "\n\n".join(op["diff_human"] for op in operations),
        "diff_machine": {"operations": operations},
        "payload": {"operations": operations},
    }
