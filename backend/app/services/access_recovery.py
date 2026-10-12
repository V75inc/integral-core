"""Offline authorization reconciliation for quarantined restored installations.

Checkpoints are private operator records outside the backup. Full raw primary
images make unknown authorization fields visible, rather than inventing defaults.
No database driver or server bootstrap is imported by the maintenance command.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError

MAX_RECORDS = 100000
SECURITY_NODES = {
    "User",
    "WorkspaceAuthorizationState",
    "Invitation",
    "ShareLink",
    "Policy",
    "Approval",
    "OwnershipProposal",
    "WorkspaceAccessGroup",
    "AppActionBinding",
    "ApplicationDefinition",
    "AgentConfig",
    "Connector",
    "RoutineTask",
}
AUTHORITY_EDGES = {
    "OWNS",
    "IS_MEMBER_OF",
    "COLLABORATES_ON",
    "EXCLUDED_FROM",
    "WorkspaceSuspension",
    "ShareGrant",
    "OwnershipGrantHistory",
    "ResourceAccessBlock",
    "HasAccessGroup",
    "AccessGroupMember",
    "HasActionBinding",
    "ActionBindingSubject",
    "HAS_POLICY",
    "HAS_APPROVAL",
    "HasOwnershipProposal",
    "HAS_SHARE_LINK",
    "INVITED_TO",
    "HAS_AGENT_CONFIG",
    "HAS_ORG_AGENT",
    "HAS_SYSTEM_AGENT",
    "IS_CONNECTED_TO",
    "HasApplicationDefinition",
    "HAS_APPROVAL_DECISION",
}
GRANT_EDGES = {
    "OWNS",
    "IS_MEMBER_OF",
    "COLLABORATES_ON",
    "EXCLUDED_FROM",
    "ShareGrant",
    "AccessGroupMember",
    "ActionBindingSubject",
}

STRUCTURAL_EDGES = {"CONTAINS", "CATALOGS", "HAS_OPERATIONAL_MODEL", "IS_OF_TYPE"}
RESOURCE_FIELDS = {
    "Workspace": ("kind",),
    "WorkspaceApp": (
        "workspace_id",
        "owner_user_id",
        "visibility",
        "lifecycle_state",
        "active_definition_id",
    ),
    "Track": ("workspace_id", "app_id", "owner_user_id", "visibility"),
    "Entry": ("track_id", "author_id", "visibility", "type_id"),
}


def digest(value: object) -> str:
    """Return stable review identity without silently accepting non-JSON values."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


async def inventory() -> dict:
    """Collect complete bounded primary rows; never truncate a recovery proof."""
    db = get_default_context().database
    result = {}
    for collection in ("node", "edge", "object"):
        rows = await db.find(collection, {}, limit=MAX_RECORDS + 1, sort=[("id", 1)])
        if len(rows) > MAX_RECORDS or any(
            not isinstance(r.get("id"), str)
            or not r["id"]
            or not isinstance(r.get("entity"), str)
            for r in rows
        ):
            raise AuthorizationUnavailableError()
        if len({r["id"] for r in rows}) != len(rows):
            raise AuthorizationUnavailableError()
        result[collection] = rows
    return result


def _private(path: Path, value: dict) -> None:
    from app.local_runtime import private_json

    path.parent.mkdir(parents=True, exist_ok=True)
    private_json(path, value)


async def checkpoint(*, installation_id: str, directory: Path, home: Path) -> dict:
    """Retain a signed latest authorization anchor outside restored/backed-up data."""
    if directory.resolve().is_relative_to(home.resolve()):
        raise ValueError(
            "Checkpoint storage must be outside the installation and backup"
        )
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    key_path = directory / "signing-key.json"
    if not key_path.exists():
        _private(key_path, {"key": secrets.token_hex(32)})
    key = json.loads(key_path.read_text())["key"]
    anchor_path = directory / "latest.json"
    old = json.loads(anchor_path.read_text()) if anchor_path.exists() else {}
    if old and old.get("installation_id") != installation_id:
        raise ValueError("Checkpoint directory belongs to another installation")
    payload = {
        "version": 1,
        "installation_id": installation_id,
        "generation": old.get("generation", 0) + 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "inventory": await inventory(),
    }
    fingerprint = digest(payload)
    signature = hmac.new(
        bytes.fromhex(key), fingerprint.encode(), hashlib.sha256
    ).hexdigest()
    filename = f"checkpoint-{payload['generation']}-{fingerprint}.json"
    _private(directory / filename, {"payload": payload, "signature": signature})
    anchor = {
        "installation_id": installation_id,
        "generation": payload["generation"],
        "digest": fingerprint,
        "file": filename,
        "signature": signature,
    }
    _private(anchor_path, anchor)
    return {k: v for k, v in anchor.items() if k != "signature"}


def current_checkpoint(directory: Path, installation_id: str) -> dict:
    """Verify the independently retained latest anchor; stale files cannot win."""
    anchor = json.loads((directory / "latest.json").read_text())
    filename = anchor.get("file", "")
    if Path(filename).name != filename or not filename:
        raise ValueError("Invalid current checkpoint file")
    record = json.loads((directory / filename).read_text())
    payload = record["payload"]
    key = json.loads((directory / "signing-key.json").read_text())["key"]
    fingerprint = digest(payload)
    signature = hmac.new(
        bytes.fromhex(key), fingerprint.encode(), hashlib.sha256
    ).hexdigest()
    if (
        payload.get("version") != 1
        or payload.get("installation_id") != installation_id
        or anchor.get("installation_id") != installation_id
        or anchor.get("generation") != payload.get("generation")
        or anchor.get("digest") != fingerprint
        or not hmac.compare_digest(record.get("signature", ""), signature)
        or not hmac.compare_digest(anchor.get("signature", ""), signature)
    ):
        raise ValueError("Checkpoint is stale, foreign or unauthenticated")
    return payload


def _authority_row(collection: str, row: dict) -> bool:
    if collection == "edge":
        return row["entity"] in AUTHORITY_EDGES
    if collection == "node":
        return row["entity"] in SECURITY_NODES
    return row["entity"] == "User" or any(
        k in row["entity"].lower()
        for k in (
            "token",
            "credential",
            "apikey",
            "oauth",
            "work",
            "trigger",
            "cleanup",
            "outbox",
            "session",
            "request",
        )
    )


def _projection(image: dict) -> dict:
    projected = {}
    for collection, rows in image.items():
        projected[collection] = [
            copy.deepcopy(r) for r in rows if _authority_row(collection, r)
        ]
    projected["resources"] = [
        {
            "id": r["id"],
            "entity": r["entity"],
            "fields": {
                k: r.get("context", {}).get(k) for k in RESOURCE_FIELDS[r["entity"]]
            },
        }
        for r in image["node"]
        if r["entity"] in RESOURCE_FIELDS
    ]
    projected["structure"] = [
        r for r in image["edge"] if r["entity"] in STRUCTURAL_EDGES
    ]
    return projected


def _review_image(projection: dict) -> dict:
    """Expose authority decisions without credential material or content bodies."""
    public = copy.deepcopy(projection)
    permitted = {
        "user_id",
        "email",
        "is_active",
        "active",
        "roles",
        "permissions",
        "status",
        "state",
        "role",
        "scope",
        "actions",
        "conditions",
        "created_by",
        "actor_id",
        "actor_kind",
        "resource_id",
        "resource_kind",
        "resource_scope",
        "workspace_id",
        "app_id",
        "expires_at",
        "revision",
        "decision_revision",
        "definition_id",
        "fingerprint",
        "name",
    }
    for collection in ("node", "object"):
        public[collection] = [
            {
                "id": row["id"],
                "entity": row["entity"],
                "context": {
                    k: v for k, v in row.get("context", {}).items() if k in permitted
                },
                "fact_digest": digest(row),
            }
            for row in projection[collection]
        ]
    return public


async def review(*, installation_id: str, directory: Path | None) -> dict:
    """Return concrete before/current membership inventory and exact apply digest."""
    restored = await inventory()
    current = current_checkpoint(directory, installation_id) if directory else None
    before = _projection(restored)
    after = _projection(current["inventory"]) if current else None
    memberships = [
        r for r in restored["edge"] if r["entity"] in {"OWNS", "IS_MEMBER_OF"}
    ]
    report = {
        "installation_id": installation_id,
        "mode": "checkpoint" if current else "credentials_disabled",
        "restored_authority": _review_image(before),
        "current_authority": _review_image(after) if after else None,
        "restored_authority_digest": digest(before),
        "current_authority_digest": digest(after),
        "memberships": memberships,
        "checkpoint_digest": digest(current) if current else "",
        "complete": True,
    }
    return {**report, "review_digest": digest(report)}


async def apply(
    *,
    installation_id: str,
    directory: Path | None,
    expected_digest: str,
    owner_ids: list[str],
    password_hash: str = "",
) -> dict:
    """Reconcile current rights, or preserve only explicitly reviewed owner access."""
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    db = get_default_context().database
    async with postgres_graph_transaction(db):
        report = await review(installation_id=installation_id, directory=directory)
        if not hmac.compare_digest(report["review_digest"], expected_digest):
            raise ValueError("Recovery review changed; review again before applying")
        image = await inventory()
        held = get_default_context().database
        if directory:
            current = current_checkpoint(directory, installation_id)["inventory"]
            if digest(_projection(image)["resources"]) != digest(
                _projection(current)["resources"]
            ) or digest(_projection(image)["structure"]) != digest(
                _projection(current)["structure"]
            ):
                raise ValueError(
                    "Resource structure changed; use explicit credentials-disabled owner recovery and review before exposure"
                )
            nodes = {r["id"] for r in image["node"]}
            for collection in ("node", "edge", "object"):
                latest = {
                    r["id"]: r
                    for r in current[collection]
                    if _authority_row(collection, r)
                }
                for row in image[collection]:
                    if not _authority_row(collection, row):
                        continue
                    new = latest.get(row["id"])
                    if new is None:
                        if collection == "node":
                            # Keep rooted forensic records but remove executable authority.
                            data = copy.deepcopy(row)
                            data["context"].update(
                                {
                                    "is_active": False,
                                    "active": False,
                                    "status": "revoked",
                                }
                            )
                            await held.save(collection, data)
                        elif collection != "edge" or row["entity"] in GRANT_EDGES:
                            await held.delete(collection, row["id"])
                    else:
                        await held.save(collection, new)
                for row in latest.values():
                    if (
                        collection == "edge"
                        and row.get("source") in nodes
                        and row.get("target") in nodes
                    ):
                        await held.save(collection, row)
            await _revoke_sessions(held, image)
        else:
            await _fallback(held, image, owner_ids, password_hash)
        receipt_id = "o.AccessRecoveryReceipt." + expected_digest
        await _record_receipt(
            held,
            {
                "id": receipt_id,
                "entity": "AccessRecoveryReceipt",
                "context": {
                    "installation_id": installation_id,
                    "review_digest": expected_digest,
                    "mode": report["mode"],
                    "reviewed_at": datetime.now(timezone.utc).isoformat(),
                },
            },
        )
    await get_default_context().clear_cache()
    return {
        "status": "reviewed",
        "receipt_id": receipt_id,
        "mode": report["mode"],
        "review_digest": expected_digest,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _record_receipt(database, record: dict) -> None:
    """Persist recovery evidence in the same primary transaction as rights."""
    await database.save("object", record)


async def _revoke_sessions(db, image: dict) -> None:
    """Revoke old inbound sessions/bearers; preserve encrypted recovery material."""
    for row in image["object"]:
        entity = row["entity"].lower()
        if any(
            k in entity
            for k in ("refreshtoken", "passwordresettoken", "apikey", "oauth")
        ):
            await db.delete("object", row["id"])


async def _fallback(db, image: dict, owner_ids: list[str], password_hash: str) -> None:
    """Disable credentials/delegation and retain reviewed canonical workspace owners."""
    if not owner_ids or len(set(owner_ids)) != len(owner_ids) or not password_hash:
        raise ValueError(
            "Recovery requires explicit canonical owners and a new password"
        )
    nodes = {r["id"]: r for r in image["node"]}
    workspaces = {r["id"] for r in image["node"] if r["entity"] == "Workspace"}
    selected = {}
    for workspace_id in workspaces:
        ownership = [
            r
            for r in image["edge"]
            if r["target"] == workspace_id and r["entity"] == "OWNS"
        ]
        membership = [
            r
            for r in image["edge"]
            if r["target"] == workspace_id
            and r["entity"] == "IS_MEMBER_OF"
            and r.get("context", {}).get("role") == "owner"
        ]
        if len(ownership) > 1 or len(membership) > 1:
            raise ValueError(
                "Ambiguous restored workspace ownership requires repair before recovery"
            )
        candidates = {r["source"] for r in ownership + membership}
        if len(candidates) != 1 or not candidates.issubset(set(owner_ids)):
            raise ValueError(
                "Review must designate each workspace's existing canonical owner"
            )
        selected[workspace_id] = candidates.pop()
    if set(selected.values()) != set(owner_ids) or any(
        nodes.get(i, {}).get("entity") != "User" for i in owner_ids
    ):
        raise ValueError("Review contains a noncanonical recovery owner")
    auth_ids = {nodes[i]["context"]["user_id"] for i in owner_ids}
    for row in image["edge"]:
        if row["entity"] == "OWNS" and nodes.get(row["target"], {}).get(
            "entity"
        ) not in {"Workspace", "WorkspaceApp", "Track", "Entry"}:
            continue
        if row["entity"] in GRANT_EDGES and not (
            row["entity"] in {"OWNS", "IS_MEMBER_OF"}
            and selected.get(row["target"]) == row["source"]
        ):
            await db.delete("edge", row["id"])
    for row in image["node"]:
        data = copy.deepcopy(row)
        context = data.get("context", {})
        if row["entity"] == "User":
            context["email_verified"] = False
            preferences = context.get("preferences", {})
            if isinstance(preferences, dict):
                preferences.pop("email_verification", None)
                preferences.pop("password_reset", None)
            await db.save("node", data)
        elif row["entity"] in SECURITY_NODES - {
            "User",
            "WorkspaceAuthorizationState",
            "ApplicationDefinition",
        }:
            context.update({"is_active": False, "active": False, "status": "revoked"})
            if row["entity"] == "ShareLink":
                context["revoked_at"] = datetime.now(timezone.utc).isoformat()
            await db.save("node", data)
        elif row["entity"] in {"WorkspaceApp", "Track"}:
            context.update({"lifecycle_state": "paused", "visibility": "private"})
            await db.save("node", data)
    for row in image["object"]:
        data = copy.deepcopy(row)
        context = data.get("context", {})
        if row["entity"] == "User":
            context["is_active"] = row["id"] in auth_ids
            context["roles"] = ["user"]
            context["permissions"] = []
            if row["id"] in auth_ids:
                context["password_hash"] = password_hash
            await db.save("object", data)
        elif _authority_row("object", row):
            context.update(
                {
                    "is_active": False,
                    "enabled": False,
                    "status": "cancelled",
                    "lease_token": "",
                    "lease_owner": "",
                }
            )
            await db.save("object", data)
    await _revoke_sessions(db, image)


async def require_reviewed_primary_receipt(marker: dict) -> None:
    """A reviewed marker must name the actual committed primary recovery unit."""
    receipt_id = marker.get("receipt_id")
    if not isinstance(
        receipt_id, str
    ) or receipt_id != "o.AccessRecoveryReceipt." + marker.get("review_digest", ""):
        raise AuthorizationUnavailableError()
    rows = await get_default_context().database.find(
        "object", {"id": receipt_id, "entity": "AccessRecoveryReceipt"}, limit=2
    )
    if len(rows) != 1 or any(
        rows[0].get("context", {}).get(k) != marker.get(k)
        for k in ("installation_id", "review_digest", "mode")
    ):
        raise AuthorizationUnavailableError()
