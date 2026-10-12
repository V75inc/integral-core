"""Account-local time, with UTC storage and explicit schedule overrides."""

import asyncio
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from jvspatial.db.database import Database

from app.api.errors import ResourceNotFoundError, ServiceUnavailableError
from app.schemas.user_timezone import UserTimezoneUpdate
from app.services.speech_preferences import resolve_user
from app.utils.time import utc_now_iso

_LOCAL_LOCKS: dict[tuple[int, int, str], asyncio.Lock] = {}


async def get_user_timezone(principal_id: str) -> str:
    """Resolve an authenticated principal's IANA zone; legacy accounts use UTC."""
    user = await resolve_user(principal_id)
    return (user.timezone if user else None) or "UTC"


async def user_time_context(principal_id: str) -> str:
    """Supply verified account-local clock context to the resident harness."""
    zone = await get_user_timezone(principal_id)
    now = datetime.now(timezone.utc).astimezone(ZoneInfo(zone))
    return (
        f"Account time zone (server-verified): {zone}. Current local time: "
        f"{now.isoformat()}; local date: {now.date().isoformat()}. "
        "Use this zone for local dates, reminders and schedules unless the user "
        "explicitly requests another zone. Absolute timestamps retain their offsets."
    )


async def set_user_timezone(
    principal_id: str, patch: UserTimezoneUpdate
) -> dict[str, Any]:
    """Update only this account field; inference cannot overwrite a saved choice.

    Native database CAS handles multi-process races. JSON/memory fallback is
    serialized for the single-process development backends, as with entry CAS.
    """
    user = await resolve_user(principal_id)
    if user is None:
        raise ResourceNotFoundError(message="User not found")
    ctx = await user.get_context()
    db = ctx.database
    concrete = db
    while getattr(concrete, "inner", None) is not None:
        concrete = concrete.inner
    lock = None
    if type(concrete).find_one_and_update is Database.find_one_and_update:
        if type(concrete).__module__ not in {"jvspatial.db.jsondb", "jvspatial.memory"}:
            raise ServiceUnavailableError(message="Atomic account updates unavailable")
        lock = _LOCAL_LOCKS.setdefault(
            (id(concrete), id(asyncio.get_running_loop()), str(user.id)), asyncio.Lock()
        )
    collection = ctx._get_collection_name("n")
    query: dict[str, Any] = {"id": user.id}
    if patch.only_if_unset:
        query["context.timezone"] = None

    async def update():
        return await db.find_one_and_update(
            collection,
            query,
            {
                "$set": {
                    "context.timezone": patch.timezone,
                    "context.updated_at": utc_now_iso(),
                }
            },
        )

    if lock is None:
        saved = await update()
    else:
        async with lock:
            saved = await update()
    # Evict snapshots even when another writer won the inference race.
    await ctx._evict_from_cache(user.id)
    changed = saved is not None
    if saved is None:
        saved = await db.find_one(collection, {"id": user.id})
    if saved is None:
        raise ResourceNotFoundError(message="User not found")
    stored = saved.get("context", {}).get("timezone")
    if changed:
        from app.services.change_event import emit_change_event

        await emit_change_event(
            actor_kind="human",
            actor_id=principal_id,
            action="user.update",
            resource_type="User",
            resource_id=user.id,
            before={"timezone": user.timezone},
            after={"timezone": stored},
            scope=f"user:{user.id}",
            details={"section": "timezone"},
        )
    return {"timezone": stored or "UTC", "configured": bool(stored)}
