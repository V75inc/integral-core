"""Transaction-held collection fence for App-owned read/check/write rules."""

from contextlib import asynccontextmanager

from app.api.errors import InsufficientPermissionsError
from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.nodes import App, Track
from app.services.entry_write_scope import (
    entry_write_scope,
    is_postgres_entry_transaction,
)
from app.services.permissions import resolve_role
from app.services.resource_location import DATA_APP_STATES, locate_resource


@asynccontextmanager
async def app_track_write_scope(ctx, track_id: str):
    """Hold the owning Track until command commit; never grant write authority.

    PostgreSQL uses the existing public transaction's no-op row update, so
    independent workers serialize without a process lock or a new reservation.
    Canonical mutations retain their own permits, revisions and receipts.
    """
    if getattr(ctx, "read_only", False):
        raise InsufficientPermissionsError(
            message="Read-only contexts cannot fence writes"
        )
    if not track_id or not ctx.workspace_id or not ctx.user_id:
        raise InsufficientPermissionsError(message="Current App scope required")
    async with entry_write_scope(f"track:{track_id}") as graph:
        with isolated_permissions_cache():

            async def admit():
                location = await locate_resource(
                    "track", track_id, allowed_app_states=DATA_APP_STATES
                )
                if location is None or location.workspace_id != ctx.workspace_id:
                    raise InsufficientPermissionsError(message="Collection unavailable")
                bound_app = str(getattr(ctx, "app_id", "") or "")
                app = await App.get(location.app_id)
                if (
                    app is None
                    or (bound_app and bound_app != location.app_id)
                    or (
                        not bound_app
                        and (
                            not ctx.bundle_slug
                            or app.installed_package_slug != ctx.bundle_slug
                        )
                    )
                    or not await resolve_role(ctx.user_id, "track", track_id)
                ):
                    raise InsufficientPermissionsError(message="Collection unavailable")

            await admit()
            if is_postgres_entry_transaction(graph.database):
                locked = await graph.database.find_one_and_update(
                    "node",
                    {"id": track_id, "entity": Track._entity_name()},
                    {"$set": {}},
                )
                if locked is None:
                    raise AuthorizationUnavailableError()
                # The fence may have waited; its previous authority proof is
                # never reused after another writer can have committed.
                with isolated_permissions_cache():
                    await admit()
            yield
