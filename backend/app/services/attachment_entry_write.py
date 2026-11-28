"""Current Entry admission for effects that change its attachments."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.nodes import Entry
from app.schemas.policy import Resource, Subject
from app.services.authorization_scope import authorization_scope
from app.services.entry_record_readonly import LOCKED_MESSAGE, entry_is_app_readonly
from app.services.entry_write_scope import (
    entry_write_scope,
    is_postgres_entry_transaction,
)
from app.services.identity_admission import identity_admission
from app.services.policy_engine import evaluate
from app.services.resource_location import DATA_APP_STATES, locate_resource


class AttachmentEntryReadonlyError(BadRequestError):
    error_code = "app_record_readonly"
    default_message = LOCKED_MESSAGE


@asynccontextmanager
async def _attachment_admission(
    principal_id: str, workspace_id: str, entry: Entry, *, transactional: bool
) -> AsyncIterator[None]:
    if principal_id == "public":
        # The public upload's commit scope revalidates its bearer separately.
        yield
        return
    from contextlib import nullcontext

    with isolated_permissions_cache():
        async with (
            (
                identity_admission((principal_id,), shared=True)
                if transactional
                else nullcontext()
            ),
            (
                authorization_scope(workspace_id, shared=True)
                if transactional
                else nullcontext()
            ),
        ):
            decision = await evaluate(
                subject=Subject(kind="human", id=principal_id),
                action="entry.update",
                resource=Resource(
                    kind="entry", id=entry.id, scope=f"track:{entry.track_id}"
                ),
                _dry_run=True,
            )
            if not decision.allowed:
                raise InsufficientPermissionsError(
                    message="Entry attachment access changed"
                )
            yield


@asynccontextmanager
async def attachment_entry_write(
    entry: Entry, principal_id: str
) -> AsyncIterator[Entry]:
    """Hold the Entry and its App rule stable from admission through the effect.

    Callers retain their existing entry or public-submission authorization.
    Re-read the Entry under its write fence before using or saving it. A
    related state tested by the owning App stays locked through commit too.
    """
    location = await locate_resource(
        "entry", entry.id, allowed_app_states=DATA_APP_STATES
    )
    if location is None or location.track_id != entry.track_id:
        raise InsufficientPermissionsError(message="Attachment entry scope changed")

    async with entry_write_scope(entry.id) as graph:
        transactional = is_postgres_entry_transaction(graph.database)
        async with _attachment_admission(
            principal_id, location.workspace_id, entry, transactional=transactional
        ):
            if transactional:
                row = await graph.database.lock_record("node", entry.id)
                current = (
                    await graph._deserialize_entity(Entry, row)
                    if row and row.get("entity") == Entry._entity_name()
                    else None
                )
            else:
                current = await Entry.find_one({"id": entry.id})
            if (
                current is None
                or current.track_id != location.track_id
                or (
                    await locate_resource(
                        "entry", entry.id, allowed_app_states=DATA_APP_STATES
                    )
                )
                != location
            ):
                raise InsufficientPermissionsError(
                    message="Attachment entry scope changed"
                )
            if await entry_is_app_readonly(
                workspace_id=location.workspace_id,
                entry_id=entry.id,
                principal_id=principal_id,
                for_write=True,
            ):
                raise AttachmentEntryReadonlyError()
            yield current
