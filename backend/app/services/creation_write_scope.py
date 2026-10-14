"""Current actor admission and complete canonical creation effect fences."""

from contextlib import AsyncExitStack
from functools import wraps
from inspect import signature
from typing import Any

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError


def canonical_creation(*, kind: str):
    """Declare the staged creation kind at the canonical graph boundary."""

    def decorate(function):
        return _canonical_creation(function, kind)

    return decorate


def _canonical_creation(function, kind: str):
    """Fence a normal or approved creator before scope resolution and graph writes."""
    parameters = signature(function)

    @wraps(function)
    async def admitted(*args: Any, **kwargs: Any):
        from app.api.errors import BadRequestError, ResourceNotFoundError
        from app.services.app_operations.event_outbox import insert_revision_event
        from app.services.app_operations.transaction_scope import (
            graph_transaction_available,
            postgres_graph_transaction,
        )
        from app.services.identity_admission import identity_admission
        from app.services.membership_write_scope import membership_write_scope
        from app.services.resource_location import (
            MANAGEMENT_APP_STATES,
            locate_resource,
        )
        from app.services.workspace_membership import _deliver
        from app.services.workspace_resolver import resolve_workspace_id

        bound = parameters.bind(*args, **kwargs)
        bound.apply_defaults()
        values = dict(bound.arguments)
        user_id = values["user_id"]
        external_sink = values.get("change_event_sink")
        events: list[dict] = []
        outboxes = []
        async with AsyncExitStack() as transaction:
            database = get_default_context().database
            if graph_transaction_available(database):
                await transaction.enter_async_context(
                    postgres_graph_transaction(database)
                )
            async with identity_admission((user_id,)):
                workspace_id = values.get("workspace_id")
                if values.get("app_id"):
                    location = await locate_resource(
                        "app",
                        values["app_id"],
                        allowed_app_states=MANAGEMENT_APP_STATES,
                    )
                    if location is None:
                        raise ResourceNotFoundError(message="Parent App unavailable")
                    if workspace_id and workspace_id != location.workspace_id:
                        raise BadRequestError(
                            message="Parent App does not match workspace"
                        )
                    workspace_id = location.workspace_id
                workspace_id = await resolve_workspace_id(
                    user_id=user_id, workspace_id=workspace_id
                )
                values["workspace_id"] = workspace_id
                values["change_event_sink"] = events.append
                async with membership_write_scope(
                    workspace_id, principal_ids=(user_id,)
                ):
                    from app.agentive.services.staging_execution import (
                        apply_bound_creation_setup,
                        capture_staging_change_event,
                        staged_command_effect,
                    )

                    targets = (
                        {"name": values["name"]}
                        if kind == "create_app"
                        else {
                            "title": values["title"],
                            "app_id": values.get("app_id"),
                        }
                    )
                    async with staged_command_effect(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        kind=kind,
                        targets=targets,
                    ) as complete:
                        result = await function(**values)
                        setup = (
                            await apply_bound_creation_setup(kind, result, user_id)
                            if complete is not None
                            else {}
                        )
                        held = get_default_context().database
                        for event in events:
                            if await capture_staging_change_event(event):
                                continue
                            if external_sink is not None:
                                external_sink(event)
                            elif graph_transaction_available(held):
                                if not event.get("resource_id"):
                                    raise AuthorizationUnavailableError()
                                outboxes.append(
                                    await insert_revision_event(
                                        transaction=held,
                                        workspace_id=workspace_id,
                                        namespace="canonical-create-v1",
                                        subject_id=event["resource_id"],
                                        revision=1,
                                        event=event,
                                    )
                                )
                        if complete is not None:
                            await complete(
                                {
                                    (
                                        "app_id" if kind == "create_app" else "track_id"
                                    ): result.id,
                                    **setup,
                                }
                            )
        # A creator's returned Node must outlive the transaction opened here.
        # For nested creation the restored context is the still-open outer
        # transaction, so subsequent writes retain its rollback boundary.
        await result.set_context(get_default_context())
        if external_sink is None:
            if outboxes:
                for outbox in outboxes:
                    await _deliver(outbox)
            elif events:
                from app.services.change_event import emit_change_event

                for event in events:
                    await emit_change_event(**event)
        from app.agentive.services.staging_execution import (
            deliver_staging_command_events,
        )

        await deliver_staging_command_events()
        return result

    return admitted
