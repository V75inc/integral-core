"""Current identity facts and conditional auth writes on the pinned runtime.

jvspatial owns tokens, passwords and authentication routes. Its shared context
can cache AuthUser and save the whole object while touching last_accessed.
This adapter retains that protocol but reads primary facts and writes only
observed changes, conditional on the identity/token state it actually read.
"""

from __future__ import annotations

import asyncio
import copy
import weakref
from typing import Any
from weakref import WeakValueDictionary

from jvspatial.api.auth.models import RefreshToken, TokenBlacklist
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.api.auth.models import UserResponse
from jvspatial.api.auth.service import AuthenticationService
from jvspatial.core.context import GraphContext, get_default_context
from jvspatial.db.database import Database
from jvspatial.db.query import QueryEngine

from app.exceptions import AuthorizationUnavailableError
from app.services.authority_facts import primary_auth_record

_local_identity_locks: WeakValueDictionary[tuple, asyncio.Lock] = WeakValueDictionary()
_OBSERVED_TYPES = (AuthUser, RefreshToken)
_USER_GUARDS = ("is_active", "email", "password_hash", "roles", "permissions")
_TOKEN_GUARDS = ("is_active", "token_hash", "token_lookup", "user_id", "expires_at")


class AuthenticationStateConflict(ValueError):
    """The current account/token no longer matches the observed auth state."""


class AuthenticationContext(GraphContext):
    """Fresh auth reads and limited CAS writes without whole-identity replacement."""

    def __init__(self, database: Any) -> None:
        super().__init__(database=database)
        self._observations: dict[int, tuple[Any, dict, dict]] = {}

    async def _remember(self, entity: Any, row: dict) -> None:
        key = id(entity)

        def forget(reference: Any) -> None:
            observed = self._observations.get(key)
            if observed is not None and observed[0] is reference:
                self._observations.pop(key, None)

        exported = await entity.export()
        self._observations[key] = (
            weakref.ref(entity, forget),
            copy.deepcopy(row.get("context") or {}),
            copy.deepcopy(exported.get("context") or {}),
        )
        await entity.set_context(self)

    async def _deserialize_entity(self, entity_class: Any, data: dict) -> Any:
        if entity_class in _OBSERVED_TYPES:
            context = data.get("context")
            # A missing lifecycle flag must not become True via model defaults.
            if not isinstance(context, dict) or not isinstance(
                context.get("is_active"), bool
            ):
                return None
        entity = await super()._deserialize_entity(entity_class, data)
        if isinstance(entity, _OBSERVED_TYPES):
            await self._remember(entity, data)
        return entity

    async def get(self, entity_class: Any, entity_id: str) -> Any:
        if entity_class not in _OBSERVED_TYPES:
            return await super().get(entity_class, entity_id)
        try:
            if entity_class is AuthUser:
                row = await primary_auth_record(self.database, entity_id)
                return (
                    await self._deserialize_entity(entity_class, row)
                    if row is not None
                    else None
                )
            rows = await self.database.find(
                "object", {"id": entity_id, "entity": entity_class._entity_name()}
            )
            if len(rows) != 1:
                return None
            return await self._deserialize_entity(entity_class, rows[0])
        except Exception as exc:
            raise AuthorizationUnavailableError() from exc

    async def save(self, entity: Any) -> Any:
        # Auth is imported while routes are registered. The operation package
        # imports those routes too; load its shared transaction seam only when
        # executing a write, after authentication registration is complete.
        from app.services.app_operations.transaction_scope import (
            graph_transaction_available,
            postgres_graph_transaction,
        )

        if not isinstance(entity, _OBSERVED_TYPES):
            return await super().save(entity)
        observed = self._observations.get(id(entity))
        if observed is None or observed[0]() is not entity:
            # New objects still use the framework create/index contract. Existing
            # objects must have an observation; a client cannot invent a baseline.
            if await self.database.find("object", {"id": entity.id}):
                raise AuthenticationStateConflict("Unobserved authentication state")
            saved = await super().save(entity)
            row = await entity.export()
            await self._remember(entity, row)
            return saved
        original, baseline = observed[1:]
        exported = await entity.export()
        proposed = exported.get("context") or {}
        changed = {
            field: value
            for field, value in proposed.items()
            if field not in baseline or baseline[field] != value
        }
        removed = set(baseline) - set(proposed)
        guards = _USER_GUARDS if isinstance(entity, AuthUser) else _TOKEN_GUARDS
        query: dict[str, Any] = {"id": entity.id, "entity": entity._entity_name()}
        for field in set(guards) | set(changed) | removed:
            query[f"context.{field}"] = (
                {"$eq": original[field]} if field in original else {"$exists": False}
            )
        update: dict[str, Any] = {
            "$set": {f"context.{field}": value for field, value in changed.items()}
        }
        if removed:
            update["$unset"] = {f"context.{field}": "" for field in removed}
        # Even a no-change save validates the original authority snapshot.
        if not changed and not removed:
            update["$set"] = {"context.is_active": original["is_active"]}
        db = self.database
        concrete = db
        seen: set[int] = set()
        while getattr(concrete, "inner", None) is not None and id(concrete) not in seen:
            seen.add(id(concrete))
            concrete = concrete.inner
        native_atomic = (
            type(concrete).find_one_and_update is not Database.find_one_and_update
        )
        if graph_transaction_available(db):
            async with postgres_graph_transaction(db) as transaction:
                identity_query = {"id": entity.id, "entity": entity._entity_name()}
                # jvspatial 0.1.1 cannot translate array equality into its SQL
                # CAS path. Lock via a scalar-only no-op first, then compare
                # the complete snapshot and patch on the same held connection.
                # The independent-worker held-disable test reproduced active
                # state restoration when the complex query used its fallback.
                locked = await transaction.find_one_and_update(
                    "object", identity_query, {"$set": {}}
                )
                if locked is None or not QueryEngine.match(locked, query):
                    raise AuthenticationStateConflict("Authentication state changed")
                row = await transaction.find_one_and_update(
                    "object", identity_query, update
                )
        elif native_atomic:
            row = await db.find_one_and_update("object", query, update)
        else:
            if type(concrete).__module__ not in {
                "jvspatial.db.jsondb",
                "jvspatial.memory",
            }:
                raise AuthorizationUnavailableError()
            # Development stores have only a local CAS seam. This is not a
            # distributed identity or authorization-release fence.
            key = (
                str(getattr(concrete, "base_path", id(concrete))),
                id(asyncio.get_running_loop()),
                str(entity.id),
            )
            lock = _local_identity_locks.setdefault(key, asyncio.Lock())
            async with lock:
                row = await db.find_one_and_update("object", query, update)
        if row is None:
            raise AuthenticationStateConflict("Authentication state changed")
        await self._remember(entity, row)
        return entity


class IntegralAuthenticationService(AuthenticationService):
    """Use current identity facts across the inherited authentication protocol."""

    @property
    def context(self) -> AuthenticationContext:
        return self._integral_context

    @context.setter
    def context(self, context: GraphContext) -> None:
        self._integral_context = (
            context
            if isinstance(context, AuthenticationContext)
            else AuthenticationContext(context.database)
        )

    async def register_user(self, user_data: Any) -> Any:
        from app.runtime.composition import require_capability

        require_capability("identity_admission")
        return await super().register_user(user_data)

    async def create_user_with_roles(self, user_data: Any) -> Any:
        from app.runtime.composition import require_capability

        require_capability("identity_admission")
        return await super().create_user_with_roles(user_data)

    async def bootstrap_admin(self, email: str, password: str, name: str = "") -> Any:
        from app.runtime.composition import require_capability

        require_capability("identity_admission")
        return await super().bootstrap_admin(email, password, name)

    async def _is_token_blacklisted_by_jti(self, token_id: str) -> bool:
        # Presence remains authoritative even if optional audit/expiry metadata
        # cannot hydrate. JWT expiry is checked before blacklist admission.
        try:
            return bool(
                await self.context.database.find(
                    "object",
                    {
                        "entity": TokenBlacklist._entity_name(),
                        "context.token_id": token_id,
                    },
                    limit=1,
                )
            )
        except Exception as exc:
            raise AuthorizationUnavailableError() from exc

    def _current_response(self, user: AuthUser) -> UserResponse:
        return UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            created_at=user.created_at,
            is_active=user.is_active,
            roles=self._get_user_roles(user),
            permissions=list(self._get_effective_permissions_for_user(user)),
        )

    async def validate_token(self, token: str) -> UserResponse | None:
        response = await super().validate_token(token)
        if response is None:
            return None
        # The upstream last_accessed write is best effort. A lost CAS must not
        # authorize the cached account it just failed to update.
        from app.runtime.composition import admits_human

        if not admits_human(response.id):
            return None
        current = await self._get_user_by_id(response.id)
        return (
            self._current_response(current)
            if current is not None and current.is_active
            else None
        )

    async def login_user(self, login_data: Any, **kwargs: Any) -> Any:
        from app.runtime.composition import require_capability

        require_capability("identity_admission")
        response = await super().login_user(login_data, **kwargs)
        current = await self._get_user_by_id(response.user.id)
        if current is None or not current.is_active:
            raise AuthenticationStateConflict("Invalid email or password")
        response.user = self._current_response(current)
        return response

    async def refresh_access_token(self, refresh_token: str, **kwargs: Any) -> Any:
        response = await super().refresh_access_token(refresh_token, **kwargs)
        current = await self._get_user_by_id(response.user.id)
        from app.runtime.composition import admits_human

        if current is None or not current.is_active or (not admits_human(current.id)):
            raise AuthenticationStateConflict("User not found or inactive")
        response.user = self._current_response(current)
        return response


def get_authentication_service() -> IntegralAuthenticationService:
    """Construct Core auth on the caller's current graph/transaction database."""
    from app.config import settings

    return IntegralAuthenticationService(
        get_default_context(),
        jwt_secret=settings.SECRET_KEY,
        jwt_algorithm=settings.ALGORITHM,
        jwt_expire_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        refresh_expire_days=settings.REFRESH_TOKEN_EXPIRE_DAYS,
        refresh_token_rotation=settings.REFRESH_TOKEN_ROTATION,
        registration_open=settings.REGISTRATION_OPEN,
    )
