"""Unregistered PostgreSQL receipt adapter for the Web Chat turn coordinator."""

from __future__ import annotations

import hashlib
import json
from secrets import compare_digest, token_hex

from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.postgres import PostgresDB

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatEventKey,
    ExternalChatReply,
    VerifiedExternalChatMessage,
)
from app.agentive.connectors.web_chat_turn import WebChatClaim, WebChatUnavailable
from app.models.web_chat_turn_receipt import WebChatTurnReceipt
from app.services.credential_crypto import (
    CIPHER_PREFIX_V1,
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
)


def _canonical(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _record_id(key: ExternalChatEventKey) -> str:
    conversation = key.conversation
    scope = conversation.provision
    identity = _canonical(
        [
            scope.workspace_id,
            scope.connector_kind,
            scope.provision_id,
            conversation.external_conversation_id,
            key.external_event_id,
        ]
    )
    return f"o.WebChatTurnReceipt.{_sha256(identity)}"


def _assignment_values(assignment: ExternalChatAssignment) -> list[str]:
    return [
        assignment.profile_key,
        assignment.publication_revision,
        assignment.route_epoch,
    ]


def _seal(value: str, aad: str) -> str:
    return encrypt_secret_for_storage(value, aad=aad)


def _open(ciphertext: str, aad: str) -> str:
    # The shared codec intentionally accepts legacy plaintext for other callers;
    # this store has never written plaintext and must not accept it.
    if not ciphertext.startswith(CIPHER_PREFIX_V1):
        raise ValueError("Expected encrypted Web Chat receipt")
    value = decrypt_secret_from_storage(ciphertext, aad=aad)
    if not value:
        raise ValueError("Web Chat receipt could not be authenticated")
    return value


def _open_request(record_id: str, ciphertext: str) -> list[str]:
    payload = json.loads(_open(ciphertext, f"{record_id}:request"))
    if (
        not isinstance(payload, list)
        or len(payload) != 5
        or not all(isinstance(value, str) and value.strip() for value in payload)
    ):
        raise ValueError("Invalid Web Chat receipt request")
    return payload


def _reply_aad(record_id: str, request_ciphertext: str) -> str:
    return f"{record_id}:reply:{_sha256(request_ciphertext)}"


class JvSpatialWebChatTurnStore:
    """Store scoped acceptance and readback across independent workers.

    Only the PostgreSQL adapter is supported: its native insert-if-absent and
    compare-and-set hold across connections and processes. The constructor
    accepts a non-transactional adapter, not a caller's transaction handle,
    so a returned fence never depends on a caller committing later. The exact
    admitted event is the record key and this store does not grant admission.

    An unresolved reservation deliberately has no reclaim timeout: if process
    work or a commit result is uncertain, retrying cannot dispatch a second
    model call. Durable recovery/retention policy is separate from this port.
    """

    def __init__(self, *, database: PostgresDB) -> None:
        if not isinstance(database, PostgresDB):
            raise TypeError("Web Chat turn receipts require PostgreSQL")
        self._database = database
        self._context = GraphContext(database=database)

    async def claim(
        self,
        message: VerifiedExternalChatMessage,
        assignment: ExternalChatAssignment,
    ) -> WebChatClaim:
        if message.key.conversation.provision != assignment.provision:
            return WebChatClaim("conflict")
        record_id = _record_id(message.key)
        request = [
            *_assignment_values(assignment),
            message.verified_sender_id,
            message.text,
        ]
        fence = token_hex(32)
        try:
            async with scoped_default_context_async(self._context):
                receipt, created = await WebChatTurnReceipt.create_if_absent(
                    id=record_id,
                    request_ciphertext=_seal(
                        _canonical(request), f"{record_id}:request"
                    ),
                    fence_digest=_sha256(fence),
                )
            if receipt.id != record_id:
                raise ValueError("Wrong Web Chat receipt identity")
            if created:
                return WebChatClaim("claimed", fence=fence)
            request_ciphertext = receipt.request_ciphertext
            if _open_request(record_id, request_ciphertext) != request:
                return WebChatClaim("conflict")
            if receipt.fence_digest and not receipt.reply_ciphertext:
                return WebChatClaim("in_progress")
            if receipt.fence_digest or not receipt.reply_ciphertext:
                raise ValueError("Invalid Web Chat receipt state")
            result = _open(
                receipt.reply_ciphertext, _reply_aad(record_id, request_ciphertext)
            )
            return WebChatClaim(
                "replayed", reply=ExternalChatReply(message.key, result)
            )
        except Exception:
            raise WebChatUnavailable("Web Chat turn receipt is unavailable") from None

    async def finish(
        self,
        key: ExternalChatEventKey,
        assignment: ExternalChatAssignment,
        fence: str,
        reply: ExternalChatReply,
    ) -> bool:
        if (
            not fence
            or reply.in_reply_to != key
            or key.conversation.provision != assignment.provision
        ):
            return False
        record_id = _record_id(key)
        try:
            document = await self._database.get("object", record_id)
            if document is None:
                return False
            state = document["context"]
            fence_digest = _sha256(fence)
            if (
                document["id"] != record_id
                or not compare_digest(state["fence_digest"], fence_digest)
                or state["reply_ciphertext"]
            ):
                return False
            request_ciphertext = state["request_ciphertext"]
            if _open_request(record_id, request_ciphertext)[:3] != _assignment_values(
                assignment
            ):
                return False
            # The pinned PostgreSQL adapter row-locks this exact predicate.
            # No preliminary read, separate log write or get/save consumes a fence.
            updated = await self._database.find_one_and_update(
                "object",
                {
                    "id": record_id,
                    "context.request_ciphertext": request_ciphertext,
                    "context.fence_digest": fence_digest,
                    "context.reply_ciphertext": "",
                },
                {
                    "$set": {
                        "context.fence_digest": "",
                        "context.reply_ciphertext": _seal(
                            reply.text, _reply_aad(record_id, request_ciphertext)
                        ),
                    }
                },
            )
            return updated is not None
        except Exception:
            raise WebChatUnavailable("Web Chat turn receipt is unavailable") from None
