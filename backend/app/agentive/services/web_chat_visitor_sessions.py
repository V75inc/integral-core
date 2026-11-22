"""Unmounted PostgreSQL-backed visitor credential mint, verify and rotate."""

from __future__ import annotations

import hashlib
import re
import time
from secrets import compare_digest, token_urlsafe
from typing import Awaitable, Callable

from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.postgres import PostgresDB

from app.agentive.connectors.external_chat import (
    ExternalChatConversation,
    ExternalChatProvision,
)
from app.agentive.services import public_web_chat
from app.agentive.services.connection_profiles import ProfileDenied
from app.agentive.services.web_chat_admission import VerifiedFullPageSession
from app.agentive.services.web_chat_browser_transport import (
    BrowserSessionAccess,
    BrowserVisitorSessions,
    StartMode,
)
from app.models.web_chat_visitor_session import WebChatVisitorSession

_TOKEN = re.compile(r"([A-Za-z0-9_-]{43})\.([A-Za-z0-9_-]{43})")
VISITOR_SESSION_TTL_SECONDS = 24 * 60 * 60
RouteReadiness = Callable[[ExternalChatProvision, str], Awaitable[bool]]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _record_id(selector: str) -> str:
    return f"o.WebChatVisitorSession.{_digest(selector)}"


class PostgresBrowserVisitorSessions:
    """Trusted host binds one request to a provision; no routes or cookies here."""

    def __init__(
        self,
        *,
        database: PostgresDB,
        readiness: RouteReadiness,
    ) -> None:
        """Require cross-process atomic persistence and exact host readiness."""
        if not isinstance(database, PostgresDB):
            raise TypeError("Web Chat visitor sessions require PostgreSQL")
        self.database = database
        self.context = GraphContext(database=database)
        self.readiness = readiness

    def bind(
        self,
        provision: ExternalChatProvision,
        route_epoch: str,
        credential: str | None,
        *,
        publication: tuple[str, int] | None = None,
    ) -> BoundVisitorSession:
        """Create per-request ports from trusted route and private credential input."""
        if (
            provision.connector_kind != "public_web_chat"
            or not isinstance(route_epoch, str)
            or not route_epoch
        ):
            raise ValueError("Expected an exact Web Chat route")
        return BoundVisitorSession(
            self, provision, route_epoch, credential, publication=publication
        )


class BoundVisitorSession:
    """One request's credentials and browser-controller ports.

    A future host may deliver newly issued credentials through its private
    credential transport. Never put this value in a response DTO or a log.
    """

    def __init__(
        self,
        store: PostgresBrowserVisitorSessions,
        provision: ExternalChatProvision,
        epoch: str,
        credential: str | None,
        *,
        publication: tuple[str, int] | None = None,
    ) -> None:
        """Keep route identity immutable for this request."""
        self._store = store
        self._provision = provision
        self._epoch = epoch
        self._credential = credential
        self._publication = publication
        self._issued: str | None = None
        self.ports = BrowserVisitorSessions(open=self.open, current=self.current)

    def take_issued_credential(self) -> str | None:
        """Consume the private result once, outside the browser reply object."""
        issued, self._issued = self._issued, None
        return issued

    async def _admit(
        self,
        visitor: str,
        existing: public_web_chat.PublicWebChatScope | None = None,
    ) -> public_web_chat.PublicWebChatScope:
        scope = await public_web_chat.admit(
            self._provision.workspace_id,
            visitor,
            existing=existing,
            expected_provision_id=self._provision.provision_id,
            transport_ready=await self._store.readiness(self._provision, self._epoch)
            is True,
        )
        if str(scope.route_generation) != self._epoch or (
            self._publication is not None
            and (scope.profile_key, scope.published_version) != self._publication
        ):
            raise ProfileDenied("Visitor route unavailable")
        return scope

    async def _read(self) -> tuple[str, dict] | None:
        token = self._credential
        match = _TOKEN.fullmatch(token) if isinstance(token, str) else None
        if match is None:
            return None
        record_id = _record_id(match.group(1))
        record = await self._store.database.get("object", record_id)
        if (
            not isinstance(record, dict)
            or record.get("id") != record_id
            or record.get("entity") != "WebChatVisitorSession"
            or not isinstance(record.get("context"), dict)
        ):
            return None
        row = record["context"]
        digest = row.get("credential_digest")
        if (
            not isinstance(digest, str)
            or not digest.isascii()
            or not compare_digest(digest, _digest(token))
            or row.get("workspace_id") != self._provision.workspace_id
            or row.get("provision_id") != self._provision.provision_id
            or row.get("route_epoch") != self._epoch
            or row.get("revoked") is not False
            or type(row.get("expires_at")) is not int
        ):
            return None
        return record_id, dict(row)

    def _stored_scope(self, row: dict) -> public_web_chat.PublicWebChatScope:
        visitor, key, version = (
            row["visitor_id"],
            row["profile_key"],
            row["published_version"],
        )
        if (
            not isinstance(visitor, str)
            or not visitor
            or not isinstance(key, str)
            or not key
            or type(version) is not int
            or version < 1
        ):
            raise ValueError("Invalid visitor-session snapshot")
        return public_web_chat.PublicWebChatScope(
            self._provision.workspace_id, visitor, key, version, int(self._epoch)
        )

    async def current(self) -> BrowserSessionAccess:
        """Verify a still authorized and unexpired session without side effects."""
        try:
            found = await self._read()
            if found is None or found[1]["expires_at"] <= int(time.time()):
                return BrowserSessionAccess("expired")
            _, row = found
            existing = self._stored_scope(row)
            scope = await self._admit(existing.visitor_id, existing)
            if await self._read() != found or row["expires_at"] <= int(time.time()):
                return BrowserSessionAccess("expired")
            return BrowserSessionAccess(
                "current",
                VerifiedFullPageSession(
                    ExternalChatConversation(self._provision, row["conversation_id"]),
                    scope,
                ),
            )
        except (ProfileDenied, KeyError, TypeError, ValueError):
            return BrowserSessionAccess("expired")
        except Exception:
            return BrowserSessionAccess("unavailable")

    async def open(self, mode: StartMode) -> BrowserSessionAccess:
        """Resume exact prior access or mint/rotate only after fresh admission."""
        if mode not in ("resume", "new"):
            return BrowserSessionAccess("unavailable")
        if mode == "resume" and self._credential is not None:
            return await self.current()
        try:
            previous = await self._read() if self._credential is not None else None
            if self._credential is not None and previous is None:
                # Invalid, revoked, or cross-route credentials never mint a session.
                return BrowserSessionAccess("expired")
            if previous is not None:
                existing = self._stored_scope(previous[1])
                await self._admit(existing.visitor_id, existing)
            selector = (
                self._credential.split(".")[0]
                if previous is not None and self._credential is not None
                else token_urlsafe(32)
            )
            token = f"{selector}.{token_urlsafe(32)}"
            visitor = token_urlsafe(32)
            scope = await self._admit(visitor)
            fields = {
                "credential_digest": _digest(token),
                "workspace_id": self._provision.workspace_id,
                "provision_id": self._provision.provision_id,
                "route_epoch": self._epoch,
                "visitor_id": visitor,
                "conversation_id": token_urlsafe(32),
                "profile_key": scope.profile_key,
                "published_version": scope.published_version,
                "expires_at": int(time.time()) + VISITOR_SESSION_TTL_SECONDS,
                "revoked": False,
            }
            record_id = _record_id(selector)
            if previous is None:
                async with scoped_default_context_async(self._store.context):
                    record, created = await WebChatVisitorSession.create_if_absent(
                        id=record_id, **fields
                    )
                if not created or record.id != record_id:
                    return BrowserSessionAccess("unavailable")
            else:
                updated = await self._store.database.find_one_and_update(
                    "object",
                    {
                        "id": record_id,
                        "entity": "WebChatVisitorSession",
                        **{
                            f"context.{key}": previous[1][key]
                            for key in (
                                "credential_digest",
                                "workspace_id",
                                "provision_id",
                                "route_epoch",
                                "revoked",
                                "expires_at",
                            )
                        },
                    },
                    {
                        "$set": {
                            f"context.{key}": value for key, value in fields.items()
                        }
                    },
                )
                if updated is None:
                    return BrowserSessionAccess("expired")
            self._credential = token
            result = await self.current()
            if result.kind == "current":
                self._issued = token
            return result
        except ProfileDenied:
            return BrowserSessionAccess("unavailable")
        except Exception:
            return BrowserSessionAccess("unavailable")
