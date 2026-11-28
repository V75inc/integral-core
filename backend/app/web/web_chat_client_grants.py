"""Detached durable grants for authorized server-to-server Web Chat clients."""

import hashlib
import re
import time
from dataclasses import dataclass, field
from secrets import compare_digest, token_urlsafe
from typing import Awaitable, Callable, Literal

from jvspatial.db.postgres import PostgresDB, PostgresTransaction
from jvspatial.db.transaction import transaction_context

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatProvision,
)
from app.agentive.services.web_chat_package import SLUG
from app.models.web_chat_client_grant import (
    WebChatClientGrantDigest,
    WebChatClientGrantRecord,
)

_SECRET = re.compile(r"iwcg_[A-Za-z0-9_-]{43}")
_GRANT_ID = re.compile(r"[A-Za-z0-9_-]{43}")
_DIGEST = re.compile(r"[a-f0-9]{64}")
_NAMESPACE = "integral.web_chat.client_grants.v1"
_RECORD = "WebChatClientGrantRecord"
_INDEX = "WebChatClientGrantDigest"


@dataclass(frozen=True)
class WebChatClientGrant:
    """Non-secret identity used to recheck ongoing client authority."""

    grant_id: str
    assignment: ExternalChatAssignment
    expires_at: int


@dataclass(frozen=True)
class WebChatGrantReview:
    """Exact proposed change; a trusted host must verify its approval.

    Choose a fresh opaque grant ID for each issuance review. Repeating the
    same reviewed action never issues a second credential.
    """

    actor_id: str
    action: Literal["issue", "revoke"]
    grant: WebChatClientGrant


@dataclass(frozen=True)
class IssuedWebChatClientGrant:
    """Private one-time delivery result; do not log or serialize it."""

    grant: WebChatClientGrant
    secret: str = field(repr=False)


@dataclass(frozen=True)
class ReviewedWebChatGrantMetadata:
    """Metadata only; current assignment is not a bearer admission result."""

    grant: WebChatClientGrant
    retirement: Literal["unretired", "expired", "retired"]
    assignment_current: bool


ReviewAuthority = Callable[[WebChatGrantReview], Awaitable[bool]]
CurrentAuthority = Callable[[ExternalChatAssignment], Awaitable[bool]]


def _text(value: str) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 255 and value == value.strip()


def _grant_shape(grant: WebChatClientGrant) -> bool:
    if not isinstance(grant, WebChatClientGrant):
        return False
    assignment = grant.assignment
    return (
        isinstance(grant.grant_id, str)
        and _GRANT_ID.fullmatch(grant.grant_id) is not None
        and type(grant.expires_at) is int
        and 0 < grant.expires_at < (1 << 63)
        and isinstance(assignment, ExternalChatAssignment)
        and assignment.provision.connector_kind == SLUG
        and all(
            _text(value)
            for value in (
                assignment.provision.workspace_id,
                assignment.provision.provision_id,
                assignment.route_epoch,
                assignment.profile_key,
                assignment.publication_revision,
            )
        )
    )


def _review_shape(review: WebChatGrantReview, action: str) -> bool:
    return (
        isinstance(review, WebChatGrantReview)
        and review.action == action
        and _text(review.actor_id)
        and _grant_shape(review.grant)
    )


def _object_id(entity: str, key: str) -> str:
    return f"o.{entity}.{key}"


def _context(row: dict | None, entity: str, identity: str) -> dict | None:
    if (
        isinstance(row, dict)
        and row.get("id") == identity
        and row.get("entity") == entity
        and isinstance(row.get("context"), dict)
    ):
        return row["context"]
    return None


@dataclass(frozen=True)
class _SavedGrant:
    grant: WebChatClientGrant
    digest: str
    revoked: bool

    @classmethod
    def from_row(cls, grant_id: str, row: dict | None) -> "_SavedGrant | None":
        data = _context(row, _RECORD, _object_id(_RECORD, grant_id))
        if data is None:
            return None
        try:
            assignment = ExternalChatAssignment(
                ExternalChatProvision(
                    data["workspace_id"],
                    data["connector_kind"],
                    data["provision_id"],
                ),
                data["profile_key"],
                data["publication_revision"],
                data["route_epoch"],
            )
            grant = WebChatClientGrant(grant_id, assignment, data["expires_at"])
            digest = data["credential_digest"]
            revoked = data["revoked"]
            if (
                _grant_shape(grant)
                and isinstance(digest, str)
                and _DIGEST.fullmatch(digest)
                and type(revoked) is bool
            ):
                return cls(grant, digest, revoked)
        except (KeyError, TypeError, ValueError):
            pass
        return None

    def record(self) -> WebChatClientGrantRecord:
        grant, assignment = self.grant, self.grant.assignment
        return WebChatClientGrantRecord(
            id=_object_id(_RECORD, grant.grant_id),
            credential_digest=self.digest,
            workspace_id=assignment.provision.workspace_id,
            connector_kind=assignment.provision.connector_kind,
            provision_id=assignment.provision.provision_id,
            route_epoch=assignment.route_epoch,
            profile_key=assignment.profile_key,
            publication_revision=assignment.publication_revision,
            expires_at=grant.expires_at,
            revoked=self.revoked,
        )

    @property
    def active(self) -> bool:
        return not self.revoked and self.grant.expires_at > int(time.time())


class PostgresWebChatClientGrants:
    """Private storage port, not a workspace permission or activation service.

    The host must independently enforce its normal manager boundary and review
    the exact actor/action/grant. Its current-authority port must resolve the
    live route and tool-free publication without trusting transport input. No
    default authorizes an action; this service mounts nothing.

    These ports run while writes hold a database transaction. Supply bounded
    nonblocking checks with independently available I/O (e.g. a separate
    pool) and no reentry into this store or its lock namespace. Review must
    revalidate the same approval without consuming it on each call; issuance
    replay is fenced by the durable grant ID instead.
    """

    def __init__(
        self,
        *,
        database: PostgresDB,
        review_authority: ReviewAuthority,
        current_authority: CurrentAuthority,
    ) -> None:
        if (
            not isinstance(database, PostgresDB)
            or not callable(review_authority)
            or not callable(current_authority)
        ):
            raise TypeError(
                "Web Chat client grants require PostgreSQL and trusted ports"
            )
        self._database = database
        self._review = review_authority
        self._current = current_authority

    async def issue(
        self, review: WebChatGrantReview
    ) -> IssuedWebChatClientGrant | None:
        """Issue one secret after exact approval; never recover it on retry."""
        if not _review_shape(review, "issue"):
            return None
        grant = review.grant
        identity = _object_id(_RECORD, grant.grant_id)
        secret = f"iwcg_{token_urlsafe(32)}"
        digest = hashlib.sha256(secret.encode("ascii")).hexdigest()
        index_id = _object_id(_INDEX, digest)
        saved = _SavedGrant(grant, digest, False)
        # Initialize the collection outside the scope of the transaction.
        await self._database.get("object", identity)
        async with transaction_context(self._database) as tx:
            if not isinstance(tx, PostgresTransaction):
                raise TypeError("Web Chat grant change requires PostgreSQL")
            await tx.advisory_lock(_NAMESPACE, identity)
            if (
                await tx.get("object", identity) is not None
                or not saved.active
                or await self._review(review) is not True
                or await self._current(grant.assignment) is not True
                or not saved.active
            ):
                return None
            await tx.advisory_lock(_NAMESPACE, index_id)
            if await tx.get("object", index_id) is not None:
                return None
            pointer = WebChatClientGrantDigest(id=index_id, grant_id=grant.grant_id)
            await tx.save("object", await pointer.export())
            await tx.save("object", await saved.record().export())
        if await self._review(review) is not True or not await self.is_current(grant):
            # Never hand out a bearer after review or live authority has been
            # lost. The committed ID still fences a retry and can be revoked.
            return None
        return IssuedWebChatClientGrant(grant, secret)

    async def _get(self, grant_id: str) -> _SavedGrant | None:
        return _SavedGrant.from_row(
            grant_id,
            await self._database.get("object", _object_id(_RECORD, grant_id)),
        )

    async def current(self, secret: str) -> WebChatClientGrant | None:
        """Authenticate a bearer by digest; no caller scope participates."""
        if not isinstance(secret, str) or _SECRET.fullmatch(secret) is None:
            return None
        digest = hashlib.sha256(secret.encode("ascii")).hexdigest()
        identity = _object_id(_INDEX, digest)
        index = _context(await self._database.get("object", identity), _INDEX, identity)
        grant_id = index.get("grant_id") if index is not None else None
        if not isinstance(grant_id, str) or _GRANT_ID.fullmatch(grant_id) is None:
            return None
        found = await self._get(grant_id)
        if found is None or not compare_digest(found.digest, digest):
            return None
        return found.grant if await self._verify(found) else None

    async def is_current(self, grant: WebChatClientGrant) -> bool:
        """Recheck a server-owned identity without retaining the original bearer."""
        if not _grant_shape(grant):
            return False
        found = await self._get(grant.grant_id)
        return found is not None and found.grant == grant and await self._verify(found)

    async def _verify(self, found: _SavedGrant) -> bool:
        return (
            found.active
            and await self._current(found.grant.assignment) is True
            and await self._get(found.grant.grant_id) == found
            and found.active
        )

    async def inspect_for_revocation(
        self, review: WebChatGrantReview
    ) -> ReviewedWebChatGrantMetadata | None:
        """Inspect one exact grant using the existing trusted revocation review.

        The host supplies independently verified manager and operator authority.
        An old assignment need not serve for maintenance to read its retirement
        status. This method never reads the digest index or returns a bearer.
        """
        if (
            not _review_shape(review, "revoke")
            or await self._review(review) is not True
        ):
            return None
        grant = review.grant
        found = await self._get(grant.grant_id)
        if found is None or found.grant != grant:
            return None
        current = await self._current(grant.assignment) is True
        if (
            await self._review(review) is not True
            or await self._get(grant.grant_id) != found
        ):
            return None
        retirement: Literal["unretired", "expired", "retired"]
        if found.revoked:
            retirement = "retired"
        elif not found.active:
            retirement = "expired"
        else:
            retirement = "unretired"
        return ReviewedWebChatGrantMetadata(grant, retirement, current)

    async def revoke(self, review: WebChatGrantReview) -> bool:
        """Durably retire the exact grant, even if its route no longer serves."""
        if not _review_shape(review, "revoke"):
            return False
        grant = review.grant
        identity = _object_id(_RECORD, grant.grant_id)
        await self._database.get("object", identity)
        async with transaction_context(self._database) as tx:
            if not isinstance(tx, PostgresTransaction):
                raise TypeError("Web Chat grant change requires PostgreSQL")
            await tx.advisory_lock(_NAMESPACE, identity)
            found = _SavedGrant.from_row(
                grant.grant_id, await tx.get("object", identity)
            )
            if (
                found is None
                or found.grant != grant
                or await self._review(review) is not True
            ):
                return False
            if not found.revoked:
                revoked = _SavedGrant(grant, found.digest, True)
                await tx.save("object", await revoked.record().export())
        return True
