"""Detached exact-ancestor embed policy for a future trusted visitor bootstrap."""

import re
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services.web_chat_package import SLUG
from app.web.full_page_chat import (
    is_canonical_web_chat_origin,
    is_web_chat_route_reference,
)

_MAX_SAFE_INTEGER = 9007199254740991
_CREDENTIAL_TEXT = re.compile(r"(?:iweb|iwes)_[A-Za-z0-9_-]{32,128}")


def _is_embed_origin(value: str) -> bool:
    if not is_canonical_web_chat_origin(value):
        return False
    hostname = urlsplit(value).hostname or ""
    return not (
        hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.startswith("127.")
        or hostname == "0.0.0.0"
        # Browsers canonicalize unusual IPv4 spellings interpreted as a
        # number. Refuse numeric terminal labels for this distinct-origin port.
        or hostname.rsplit(".", 1)[-1].isdecimal()
        or hostname.rsplit(".", 1)[-1].startswith("0x")
    )


def _version(value: int) -> bool:
    return type(value) is int and 1 <= value <= _MAX_SAFE_INTEGER


def _identity(value: str) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 256 and value == value.strip()


@dataclass(frozen=True)
class ManagedWebChatEmbedPolicy:
    """Private host attestation for one embed reference and exact active policy."""

    provision: ExternalChatProvision
    route_epoch: str
    profile_key: str
    published_version: int
    policy_revision: int
    parent_origin: str
    embed_origin: str
    embed_reference: str

    def valid(self) -> bool:
        """Require canonical cross-origin HTTPS and exact scope/revisions."""
        return (
            isinstance(self.provision, ExternalChatProvision)
            and self.provision.connector_kind == SLUG
            and _identity(self.provision.workspace_id)
            and _identity(self.provision.provision_id)
            and isinstance(self.route_epoch, str)
            and 1 <= len(self.route_epoch) <= 20
            and self.route_epoch.isascii()
            and self.route_epoch.isdecimal()
            and str(int(self.route_epoch)) == self.route_epoch
            and _identity(self.profile_key)
            and _version(self.published_version)
            and _version(self.policy_revision)
            and _is_embed_origin(self.parent_origin)
            and _is_embed_origin(self.embed_origin)
            and self.parent_origin != self.embed_origin
            and is_web_chat_route_reference(self.embed_reference)
            and not _CREDENTIAL_TEXT.search(self.embed_reference)
        )

    def matches(self, *, origin: str, path: str) -> bool:
        """Accept only this canonical origin and exact dedicated embed path."""
        return (
            self.valid()
            and origin == self.embed_origin
            and path == f"/embed/{self.embed_reference}"
        )


ReadCurrentEmbedPolicy = Callable[[str], Awaitable[ManagedWebChatEmbedPolicy | None]]


async def check_current_web_chat_embed_policy(
    *,
    expected: ManagedWebChatEmbedPolicy,
    served_origin: str,
    raw_path: str,
    read_current: ReadCurrentEmbedPolicy,
) -> bool:
    """Check current authority, not visitor identity, session, or admission.

    The trusted caller supplies server-approved context, never values derived
    from a parent message, cookie, referer or visitor-selected privileges.
    Its fresh read must verify current readiness, active assignment and policy
    revocation; a failed authority read supplies no usable proof.
    """
    if not isinstance(expected, ManagedWebChatEmbedPolicy) or not expected.matches(
        origin=served_origin, path=raw_path
    ):
        return False
    current = await read_current(expected.embed_reference)
    return (
        isinstance(current, ManagedWebChatEmbedPolicy)
        and current.valid()
        and current == expected
    )
