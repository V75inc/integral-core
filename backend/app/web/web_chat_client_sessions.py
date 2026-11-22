"""Detached PostgreSQL session credentials for approved Web Chat API clients."""

import hashlib
import re
import time
from dataclasses import dataclass, field
from secrets import token_urlsafe
from typing import Awaitable, Callable

from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.postgres import PostgresDB

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatConversation,
    ExternalChatProvision,
)
from app.models.web_chat_client_session import WebChatClientSessionRecord
from app.web.web_chat_client_grants import (
    PostgresWebChatClientGrants,
    WebChatClientGrant,
)

MAX_CLIENT_SESSION_TTL_SECONDS = 900
_SECRET = re.compile(r"iwcs_[A-Za-z0-9_-]{43}")
_KEY = re.compile(r"[A-Za-z0-9_-]{43}")
_ENTITY = "WebChatClientSessionRecord"


@dataclass(frozen=True)
class WebChatClientSession:
    """Exact non-bearer projection for a trusted request-bound transport."""

    grant: WebChatClientGrant
    conversation: ExternalChatConversation
    visitor_id: str
    expires_at: int


@dataclass(frozen=True)
class IssuedWebChatClientSession:
    """Private creation result; only the dedicated bearer transport may deliver it."""

    session: WebChatClientSession
    secret: str = field(repr=False)


ClientSessionStartBudget = Callable[[WebChatClientGrant], Awaitable[bool]]


def _record_id(secret: str) -> str:
    digest = hashlib.sha256(secret.encode("ascii")).hexdigest()
    return f"o.{_ENTITY}.{digest}"


def _key(value: str) -> bool:
    return isinstance(value, str) and _KEY.fullmatch(value) is not None


def _load(identity: str, row: dict | None) -> WebChatClientSession | None:
    if (
        not isinstance(row, dict)
        or row.get("entity") != _ENTITY
        or row.get("id") != identity
        or not isinstance(row.get("context"), dict)
    ):
        return None
    fields = row["context"]
    try:
        provision = ExternalChatProvision(
            fields["workspace_id"],
            fields["connector_kind"],
            fields["provision_id"],
        )
        assignment = ExternalChatAssignment(
            provision,
            fields["profile_key"],
            fields["publication_revision"],
            fields["route_epoch"],
        )
        grant_id = fields["grant_id"]
        visitor = fields["visitor_id"]
        conversation = fields["conversation_id"]
        issued, expires, grant_expires = (
            fields["issued_at"],
            fields["expires_at"],
            fields["grant_expires_at"],
        )
        if (
            not all(_key(value) for value in (grant_id, visitor, conversation))
            or visitor == conversation
            or any(type(value) is not int for value in (issued, expires, grant_expires))
            or not 0 < issued <= int(time.time()) < expires <= grant_expires
            or not 1 <= expires - issued <= MAX_CLIENT_SESSION_TTL_SECONDS
        ):
            return None
        return WebChatClientSession(
            grant=WebChatClientGrant(grant_id, assignment, grant_expires),
            conversation=ExternalChatConversation(provision, conversation),
            visitor_id=visitor,
            expires_at=expires,
        )
    except (KeyError, TypeError, ValueError):
        return None


class PostgresWebChatClientSessions:
    """Persist independent brief sessions after exact live grant and budget checks.

    No OAuth, staff token, raw client bearer, origin, or visitor cookie is an
    alternative here. The host resolves a client grant before calling open and
    provides a separate shared start budget scoped to that exact grant. A later
    serving adapter must call current again before delivering history/replies.
    The store is intentionally unregistered and does not serve HTTP.
    """

    def __init__(
        self,
        *,
        database: PostgresDB,
        grants: PostgresWebChatClientGrants,
        start_budget: ClientSessionStartBudget,
        ttl_seconds: int = MAX_CLIENT_SESSION_TTL_SECONDS,
    ) -> None:
        if (
            not isinstance(database, PostgresDB)
            or not isinstance(grants, PostgresWebChatClientGrants)
            or not callable(start_budget)
        ):
            raise TypeError("Web Chat sessions require PostgreSQL and trusted ports")
        if (
            type(ttl_seconds) is not int
            or not 1 <= ttl_seconds <= MAX_CLIENT_SESSION_TTL_SECONDS
        ):
            raise ValueError("Invalid Web Chat client session lifetime")
        self._database = database
        self._context = GraphContext(database=database)
        self._grants = grants
        self._start_budget = start_budget
        self._ttl = ttl_seconds

    async def open(
        self, grant: WebChatClientGrant
    ) -> IssuedWebChatClientSession | None:
        """Budget and mint one new session only for a freshly current grant."""
        if (
            not isinstance(grant, WebChatClientGrant)
            or await self._grants.is_current(grant) is not True
            or await self._start_budget(grant) is not True
            or await self._grants.is_current(grant) is not True
        ):
            return None
        issued = int(time.time())
        expiry = min(issued + self._ttl, grant.expires_at)
        if not 0 < issued < expiry:
            return None

        assignment = grant.assignment
        provision = assignment.provision
        secret = f"iwcs_{token_urlsafe(32)}"
        identity = _record_id(secret)
        visitor, conversation = token_urlsafe(32), token_urlsafe(32)
        if visitor == conversation:
            return None
        async with scoped_default_context_async(self._context):
            record, created = await WebChatClientSessionRecord.create_if_absent(
                id=identity,
                grant_id=grant.grant_id,
                workspace_id=provision.workspace_id,
                connector_kind=provision.connector_kind,
                provision_id=provision.provision_id,
                route_epoch=assignment.route_epoch,
                profile_key=assignment.profile_key,
                publication_revision=assignment.publication_revision,
                grant_expires_at=grant.expires_at,
                visitor_id=visitor,
                conversation_id=conversation,
                issued_at=issued,
                expires_at=expiry,
            )
        if not created or record.id != identity:
            return None
        expected = WebChatClientSession(
            grant, ExternalChatConversation(provision, conversation), visitor, expiry
        )
        if await self.current(secret) != expected:
            # A lost grant or expired snapshot after durable storage cannot
            # disclose the bearer. The orphan record is unusable and expires.
            return None
        return IssuedWebChatClientSession(expected, secret)

    async def current(self, secret: str) -> WebChatClientSession | None:
        """Read exact persisted authority with no scope or credential fallback."""
        if not isinstance(secret, str) or _SECRET.fullmatch(secret) is None:
            return None
        identity = _record_id(secret)
        found = _load(identity, await self._database.get("object", identity))
        if (
            found is None
            or await self._grants.is_current(found.grant) is not True
            or _load(identity, await self._database.get("object", identity)) != found
        ):
            return None
        return found
