"""Detached PostgreSQL embed bootstrap; separate from full-page sessions."""

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from secrets import compare_digest, token_urlsafe

from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.postgres import PostgresDB

from app.models.web_chat_embed_credential import WebChatEmbedCredential
from app.web.web_chat_embed_policy import (
    ManagedWebChatEmbedPolicy,
    ReadCurrentEmbedPolicy,
    check_current_web_chat_embed_policy,
)

SEED_TTL_SECONDS = 60
SESSION_TTL_SECONDS = 300
_TOKEN = re.compile(r"(iweb_|iwes_)([A-Za-z0-9_-]{43})([A-Za-z0-9_-]{43})")
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _record_id(selector: str) -> str:
    return f"o.WebChatEmbedCredential.{_hash(selector)}"


def _scope_digest(policy: ManagedWebChatEmbedPolicy) -> str:
    return _hash(
        json.dumps(
            (
                policy.provision.workspace_id,
                policy.provision.connector_kind,
                policy.provision.provision_id,
                policy.route_epoch,
                policy.profile_key,
                policy.published_version,
                policy.policy_revision,
                policy.parent_origin,
                policy.embed_origin,
                policy.embed_reference,
            ),
            separators=(",", ":"),
        )
    )


@dataclass(frozen=True)
class EmbedVisitorContext:
    """Unprivileged visitor identity allocated by the detached embed boundary."""

    policy: ManagedWebChatEmbedPolicy
    visitor_id: str = field(repr=False)
    conversation_id: str = field(repr=False)
    expires_at: int


@dataclass(frozen=True)
class IssuedEmbedSession:
    """Expose the private replacement only to the embedding transport owner."""

    credential: str = field(repr=False)
    visitor: EmbedVisitorContext


class PostgresWebChatEmbedCredentials:
    """Own short-lived embed seed issue, one exchange, and current-session lookup.

    Not an endpoint, cookie or full-page admission API. The future host must
    admit and rate-limit issuance and implement credential-safe delivery,
    retention, and independent request authorization around this store.
    """

    def __init__(
        self, *, database: PostgresDB, read_current_policy: ReadCurrentEmbedPolicy
    ) -> None:
        """Require exact host policy evidence and durable atomic storage."""
        if not isinstance(database, PostgresDB) or not callable(read_current_policy):
            raise TypeError("Embed credentials require PostgreSQL and host policy")
        self._db = database
        self._context = GraphContext(database=database)
        self._read_policy = read_current_policy

    def bind(
        self, *, policy: ManagedWebChatEmbedPolicy, served_origin: str, raw_path: str
    ) -> "BoundWebChatEmbedCredentials":
        """Bind an expected host-resolved route, not a caller's authority claims."""
        return BoundWebChatEmbedCredentials(
            self, policy=policy, served_origin=served_origin, raw_path=raw_path
        )


class BoundWebChatEmbedCredentials:
    """One trusted embed origin/path and expected active policy snapshot."""

    def __init__(
        self,
        owner: PostgresWebChatEmbedCredentials,
        *,
        policy: ManagedWebChatEmbedPolicy,
        served_origin: str,
        raw_path: str,
    ) -> None:
        """Hold immutable-by-contract host context for each current-policy check."""
        self._owner = owner
        self._policy = policy
        self._origin = served_origin
        self._path = raw_path

    async def _ready(self) -> bool:
        return await check_current_web_chat_embed_policy(
            expected=self._policy,
            served_origin=self._origin,
            raw_path=self._path,
            read_current=self._owner._read_policy,
        )

    async def issue_seed(self) -> str | None:
        """Mint a private short-lived seed only for the current exact host scope."""
        if not await self._ready():
            return None
        selector = token_urlsafe(32)
        seed = f"iweb_{selector}{token_urlsafe(32)}"
        identity = _record_id(selector)
        expires_at = int(time.time()) + SEED_TTL_SECONDS
        async with scoped_default_context_async(self._owner._context):
            record, created = await WebChatEmbedCredential.create_if_absent(
                id=identity,
                scope_digest=_scope_digest(self._policy),
                credential_digest=_hash(seed),
                kind="seed",
                expires_at=expires_at,
            )
        if (
            not created
            or record.id != identity
            or not await self._ready()
            or expires_at <= int(time.time())
        ):
            return None
        return seed

    async def _lookup(
        self, token: str, prefix: str, kind: str
    ) -> tuple[str, dict] | None:
        match = _TOKEN.fullmatch(token) if isinstance(token, str) else None
        if match is None or match.group(1) != prefix:
            return None
        identity = _record_id(match.group(2))
        row = await self._owner._db.get("object", identity)
        if (
            not isinstance(row, dict)
            or row.get("id") != identity
            or row.get("entity") != "WebChatEmbedCredential"
            or not isinstance(row.get("context"), dict)
        ):
            return None
        data = row["context"]
        digest = data.get("credential_digest")
        expires_at = data.get("expires_at")
        ttl = SEED_TTL_SECONDS if kind == "seed" else SESSION_TTL_SECONDS
        now = int(time.time())
        if (
            data.get("scope_digest") != _scope_digest(self._policy)
            or data.get("kind") != kind
            or type(expires_at) is not int
            or not now < expires_at <= now + ttl
            or not isinstance(digest, str)
            or not _DIGEST.fullmatch(digest)
            or not compare_digest(digest, _hash(token))
        ):
            return None
        return match.group(2), data

    async def consume_seed(self, seed: str) -> IssuedEmbedSession | None:
        """Atomically burn the exact seed and create one separate visitor session."""
        if not await self._ready():
            return None
        candidate = await self._lookup(seed, "iweb_", "seed")
        if candidate is None or not await self._ready():
            return None
        selector, current = candidate
        now = int(time.time())
        if current["expires_at"] <= now:
            return None
        credential = f"iwes_{selector}{token_urlsafe(32)}"
        fields = {
            "credential_digest": _hash(credential),
            "kind": "session",
            "expires_at": now + SESSION_TTL_SECONDS,
            "visitor_id": token_urlsafe(32),
            "conversation_id": token_urlsafe(32),
        }
        updated = await self._owner._db.find_one_and_update(
            "object",
            {
                "id": _record_id(selector),
                "entity": "WebChatEmbedCredential",
                **{
                    f"context.{key}": current[key]
                    for key in (
                        "scope_digest",
                        "credential_digest",
                        "kind",
                        "expires_at",
                    )
                },
            },
            {"$set": {f"context.{key}": value for key, value in fields.items()}},
        )
        if updated is None:
            return None
        session = await self.current(credential)
        # A lock or slow policy read may outlast the seed. Never release the
        # replacement in that case; an uncertain/expired exchange is burned.
        if session is None or current["expires_at"] <= int(time.time()):
            return None
        return IssuedEmbedSession(credential, session)

    async def current(self, credential: str) -> EmbedVisitorContext | None:
        """Read the exact current embed-only session; never refresh its expiry."""
        if not await self._ready():
            return None
        found = await self._lookup(credential, "iwes_", "session")
        if found is None:
            return None
        _, data = found
        visitor, conversation = data.get("visitor_id"), data.get("conversation_id")
        if (
            not isinstance(visitor, str)
            or not isinstance(conversation, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", visitor)
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", conversation)
            or not await self._ready()
            or data["expires_at"] <= int(time.time())
        ):
            return None
        return EmbedVisitorContext(
            self._policy, visitor, conversation, data["expires_at"]
        )
