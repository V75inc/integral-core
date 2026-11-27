"""Detached PostgreSQL persistence for explicitly host-approved Web Chat references."""

import hashlib
import json
import re
from dataclasses import dataclass
from secrets import token_urlsafe
from typing import Awaitable, Callable

from jvspatial.db.postgres import PostgresDB, PostgresTransaction
from jvspatial.db.transaction import transaction_context

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services.web_chat_package import SLUG
from app.models.web_chat_route_reference import WebChatRouteKey, WebChatRouteReference
from app.web.full_page_chat import is_canonical_web_chat_origin
from app.web.web_chat_route_authority import ApprovedWebChatReference

_KEY = re.compile(r"[A-Za-z0-9_-]{43}")
_EPOCH = re.compile(r"0|[1-9][0-9]{0,19}")
_NAMESPACE = "integral.web_chat.route_reference.v1"


def _id(entity: str, *parts: str) -> str:
    digest = hashlib.sha256(
        json.dumps(parts, separators=(",", ":")).encode()
    ).hexdigest()
    return f"o.{entity}.{digest}"


def _scope_id(scope: ExternalChatProvision) -> str:
    return _id("WebChatRouteReference", scope.workspace_id, scope.provision_id)


def _key_id(key: str) -> str:
    return _id("WebChatRouteKey", key)


def _text(value: str) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 128 and value == value.strip()


def _scope(scope: ExternalChatProvision) -> bool:
    return (
        isinstance(scope, ExternalChatProvision)
        and scope.connector_kind == SLUG
        and _text(scope.workspace_id)
        and _text(scope.provision_id)
    )


def _approval(value: ApprovedWebChatReference) -> bool:
    return (
        isinstance(value.key, str)
        and _KEY.fullmatch(value.key) is not None
        and is_canonical_web_chat_origin(value.origin)
        and isinstance(value.epoch, str)
        and _EPOCH.fullmatch(value.epoch) is not None
        and _text(value.profile_key)
        and type(value.published_version) is int
        and value.published_version > 0
    )


def _context(row: dict, entity: str, identity: str) -> dict:
    if (
        not isinstance(row, dict)
        or row.get("entity") != entity
        or row.get("id") != identity
        or not isinstance(row.get("context"), dict)
    ):
        raise ValueError("Invalid Web Chat reference record")
    return row["context"]


def _pointed_scope(row: dict, key_id: str) -> ExternalChatProvision:
    data = _context(row, "WebChatRouteKey", key_id)
    return ExternalChatProvision(
        data.get("workspace_id"), SLUG, data.get("provision_id")
    )


@dataclass(frozen=True)
class WebChatReferenceSnapshot:
    """Private CAS revision and its exact approval, if currently issued."""

    revision: int
    approved: ApprovedWebChatReference | None


def _snapshot(
    scope: ExternalChatProvision, row: dict | None
) -> WebChatReferenceSnapshot:
    if row is None:
        return WebChatReferenceSnapshot(0, None)
    data = _context(row, "WebChatRouteReference", _scope_id(scope))
    revision = data.get("revision")
    if (
        data.get("workspace_id") != scope.workspace_id
        or data.get("provision_id") != scope.provision_id
        or type(revision) is not int
        or revision < 1
    ):
        raise ValueError("Invalid Web Chat reference scope")
    key = data.get("key")
    if key is None:
        return WebChatReferenceSnapshot(revision, None)
    value = ApprovedWebChatReference(
        key,
        data.get("origin"),
        scope,
        data.get("epoch"),
        data.get("profile_key"),
        data.get("published_version"),
    )
    if not _approval(value):
        raise ValueError("Invalid Web Chat reference approval")
    return WebChatReferenceSnapshot(revision, value)


class PostgresWebChatReferenceStore:
    """Private trusted host issuer and reader; not an activation or policy service.

    An owning host supplies the approved origin and exact publication/epoch
    after independently authorizing the change. The route authority separately
    checks that approval against the durable managed route before serving.
    """

    def __init__(
        self,
        *,
        database: PostgresDB,
        install_readiness: Callable[[str], Awaitable[bool]],
        transport_readiness: Callable[[ApprovedWebChatReference], Awaitable[bool]],
    ) -> None:
        """Require supported atomic storage and explicit trusted readiness."""
        if (
            not isinstance(database, PostgresDB)
            or not callable(install_readiness)
            or not callable(transport_readiness)
        ):
            raise TypeError("Web Chat references require PostgreSQL and trusted ports")
        self._database = database
        self._install = install_readiness
        self._transport = transport_readiness

    async def install_ready(self, workspace_id: str) -> bool:
        """Ask the infrastructure owner about this exact workspace."""
        return _text(workspace_id) and await self._install(workspace_id) is True

    async def transport_ready(self, approved: ApprovedWebChatReference) -> bool:
        """Ask the infrastructure owner about this still-current approval."""
        return (
            isinstance(approved, ApprovedWebChatReference)
            and await self.by_provision(approved.provision) == approved
            and await self._transport(approved) is True
        )

    async def observe(self, scope: ExternalChatProvision) -> WebChatReferenceSnapshot:
        """Read owning revision for private CAS; fail on corrupt storage."""
        if not _scope(scope):
            raise ValueError("Invalid Web Chat provision")
        return _snapshot(scope, await self._database.get("object", _scope_id(scope)))

    async def by_provision(
        self, provision: ExternalChatProvision
    ) -> ApprovedWebChatReference | None:
        """Read an approval only when both primary-key indexes agree."""
        try:
            found = (await self.observe(provision)).approved
            if found is None:
                return None
            identity = _key_id(found.key)
            pointer = await self._database.get("object", identity)
            return found if _pointed_scope(pointer, identity) == provision else None
        except ValueError:
            return None

    async def by_key(self, key: str) -> ApprovedWebChatReference | None:
        """Use at most two primary-key reads; refuse stale/inconsistent pointers."""
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            return None
        identity = _key_id(key)
        row = await self._database.get("object", identity)
        if row is None:
            return None
        try:
            scope = _pointed_scope(row, identity)
            found = (await self.observe(scope)).approved
        except ValueError:
            return None
        return found if found is not None and found.key == key else None

    async def issue(
        self,
        provision: ExternalChatProvision,
        *,
        expected_revision: int,
        origin: str,
        epoch: str,
        profile_key: str,
        published_version: int,
    ) -> WebChatReferenceSnapshot | None:
        """Issue or replace a host-approved reference, never reusing a key."""
        value = ApprovedWebChatReference(
            token_urlsafe(32), origin, provision, epoch, profile_key, published_version
        )
        if not _approval(value):
            raise ValueError("Invalid Web Chat host approval")
        return await self._replace(provision, expected_revision, value)

    async def revoke(
        self, provision: ExternalChatProvision, *, expected_revision: int
    ) -> WebChatReferenceSnapshot | None:
        """Retire reference and retain revision so a stale writer cannot reissue."""
        return await self._replace(provision, expected_revision, None)

    async def _replace(
        self,
        scope: ExternalChatProvision,
        revision: int,
        next_value: ApprovedWebChatReference | None,
    ) -> WebChatReferenceSnapshot | None:
        if not _scope(scope) or type(revision) is not int or revision < 0:
            raise ValueError("Invalid Web Chat reference change")
        identity = _scope_id(scope)
        async with transaction_context(self._database) as tx:
            if not isinstance(tx, PostgresTransaction):
                raise TypeError("Web Chat reference change requires PostgreSQL")
            await tx.advisory_lock(_NAMESPACE, identity)
            current = _snapshot(scope, await tx.get("object", identity))
            if current.revision != revision:
                return None
            if next_value is not None:
                pointer_id = _key_id(next_value.key)
                # The purpose of this lock is collision safety across different
                # provisions. Every issuer takes provision lock before key lock.
                await tx.advisory_lock(_NAMESPACE, pointer_id)
                if await tx.get("object", pointer_id) is not None:
                    raise ValueError("Web Chat reference could not be issued")
                pointer = WebChatRouteKey(
                    id=pointer_id,
                    workspace_id=scope.workspace_id,
                    provision_id=scope.provision_id,
                )
                await tx.save("object", await pointer.export())
            if current.approved is not None:
                old_pointer = _key_id(current.approved.key)
                old_row = await tx.get("object", old_pointer)
                if (
                    old_row is not None
                    and _pointed_scope(old_row, old_pointer) != scope
                ):
                    raise ValueError("Invalid Web Chat reference pointer scope")
                await tx.delete("object", old_pointer)
            record = WebChatRouteReference(
                id=identity,
                workspace_id=scope.workspace_id,
                provision_id=scope.provision_id,
                revision=revision + 1,
                key=next_value.key if next_value else None,
                origin=next_value.origin if next_value else None,
                epoch=next_value.epoch if next_value else None,
                profile_key=next_value.profile_key if next_value else None,
                published_version=next_value.published_version if next_value else None,
            )
            await tx.save("object", await record.export())
        return WebChatReferenceSnapshot(revision + 1, next_value)
