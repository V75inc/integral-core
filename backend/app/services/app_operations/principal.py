"""Current human aliases for operation identity, separate from actor provenance."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from app.exceptions import AuthorizationUnavailableError
from app.schemas.policy import Subject


@dataclass(frozen=True)
class OperationPrincipal:
    subject: Subject
    principal_id: str
    aliases: tuple[str, ...] = ()
    profile_id: str = ""

    @asynccontextmanager
    async def admit(self) -> AsyncIterator[None]:
        """Revalidate after receipt locks, preserving the established lock order.

        Earlier workers claim receipts before effect identity admission. Taking
        identity locks before alias receipt locks would invert that order.
        """
        if self.subject.kind != "human":
            yield
            return
        from app.services.identity_admission import identity_admission

        async with identity_admission((self.subject.id,)):
            current = await resolve_operation_principal(self.subject)
            if (
                current.principal_id != self.principal_id
                or current.aliases != self.aliases
                or current.profile_id != self.profile_id
            ):
                raise AuthorizationUnavailableError()
            yield


async def resolve_operation_principal(subject: Subject) -> OperationPrincipal:
    """Only a fresh linked human identity supplies aliases; never its delegates."""
    if subject.kind != "human":
        return OperationPrincipal(subject.model_copy(deep=True), subject.id)
    from jvspatial.core.context import get_default_context

    from app.api.errors import InsufficientPermissionsError
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.nodes import User
    from app.services.authority_facts import primary_node_record, required_node_text
    from app.services.permissions import get_user_node

    with isolated_permissions_cache():
        profile = await get_user_node(subject.id)
        if profile is None:
            raise InsufficientPermissionsError(
                message="Current operation identity required"
            )
        row = await primary_node_record(
            get_default_context().database, User, profile.id
        )
        if row is None:
            raise AuthorizationUnavailableError()
        auth_id = required_node_text(row, "user_id", allow_empty=True)
        preferred = auth_id or profile.id
        return OperationPrincipal(
            subject.model_copy(deep=True),
            preferred,
            (profile.id,) if preferred != profile.id else (),
            profile.id,
        )
