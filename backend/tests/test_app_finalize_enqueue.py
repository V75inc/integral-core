"""Settings validation must not permanently reserve an install work identity."""

import pytest

from app.agentive.services.work_items import enqueue_work_item
from app.agentive.work_models import WorkItem
from app.api.errors import ResourceConflictError
from app.exceptions import AppInstallTokenInvalidError, OperationalModelValidationError
from app.services.app_install_token import issue_install_token
from app.services.app_lifecycle import enqueue_finalize_install_work
from app.services.app_service import create_app_for_user
from tests.fixtures.workspaces import make_org_workspace, workspace_owner_id


async def pending_app():
    """Create a reachable App with a real active definition and settings gate."""
    workspace = await make_org_workspace("settings-workspace")
    owner_id = await workspace_owner_id(workspace)
    app = await create_app_for_user(
        owner_id, "Settings test", workspace_id=workspace.id
    )
    app.lifecycle_state = "awaiting_settings"
    app.settings_schema = {
        "type": "object",
        "properties": {"currency": {"type": "string", "minLength": 1}},
        "required": ["currency"],
    }
    await app.save()
    return app


async def legacy_submission(app, token, status, failure=None):
    """Retain a pre-fix submission using the real durable enqueue path."""
    work = await enqueue_work_item(
        kind="app_lifecycle",
        origin="app_lifecycle",
        principal_id="settings-owner",
        workspace_id=app.workspace_id,
        app_id=app.id,
        definition_id=app.active_definition_id,
        idempotency_key=f"finalize_install:{app.id}:{app.active_definition_revision}",
        input_payload={
            "action": "finalize_install",
            "install_token": token,
            "settings": {},
        },
    )
    work.status = status
    work.failure = failure
    await work.save()
    return work


@pytest.mark.asyncio
async def test_invalid_settings_or_token_do_not_reserve_work():
    """Reject invalid input without poisoning the revision's durable identity."""
    app = await pending_app()
    with pytest.raises(OperationalModelValidationError):
        await enqueue_finalize_install_work(
            app_node=app,
            actor_id="settings-owner",
            install_token=issue_install_token(app.id),
            settings={},
        )
    with pytest.raises(AppInstallTokenInvalidError):
        await enqueue_finalize_install_work(
            app_node=app,
            actor_id="settings-owner",
            install_token="invalid",
            settings={"currency": "USD"},
        )
    assert not await WorkItem.find({"context.app_id": app.id})


@pytest.mark.asyncio
async def test_corrected_settings_retry_legacy_validation_failure_idempotently():
    """Correct a known pre-effect failure without duplicating corrected work."""
    app = await pending_app()
    old = await legacy_submission(
        app,
        issue_install_token(app.id),
        "failed",
        {
            "code": "operational_model_validation_error",
            "message": "Settings failed schema validation: 'currency' is a required property",
        },
    )
    token = issue_install_token(app.id)
    kwargs = {
        "app_node": app,
        "actor_id": "settings-owner",
        "install_token": token,
        "settings": {"currency": "USD"},
    }
    first = await enqueue_finalize_install_work(**kwargs)
    second = await enqueue_finalize_install_work(**kwargs)
    assert first == second
    assert first["work_item_id"] != old.work_item_id
    records = await WorkItem.find({"context.app_id": app.id})
    assert len(records) == 2
    assert (await WorkItem.get(old.id)).status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,failure",
    [
        ("queued", None),
        ("succeeded", None),
        (
            "failed",
            {
                "code": "unknown_outcome",
                "message": "Settings failed schema validation:",
            },
        ),
        (
            "failed",
            {
                "code": "operational_model_validation_error",
                "message": "Seed validation failed",
            },
        ),
    ],
)
async def test_conflicting_submission_does_not_repeat_uncertain_effects(
    status, failure
):
    """Keep occupied work identities and expose conflicts as HTTP errors."""
    app = await pending_app()
    await legacy_submission(app, issue_install_token(app.id), status, failure)
    with pytest.raises(ResourceConflictError) as caught:
        await enqueue_finalize_install_work(
            app_node=app,
            actor_id="settings-owner",
            install_token=issue_install_token(app.id),
            settings={"currency": "USD"},
        )
    assert caught.value.status_code == 409
    assert len(await WorkItem.find({"context.app_id": app.id})) == 1
