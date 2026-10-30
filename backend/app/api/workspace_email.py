"""Authenticated native workspace email settings and signed delivery webhooks."""

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import BadRequestError
from app.api.utils import resolve_principal_id
from app.schemas.workspace_email import EmailRevision, SaveEmailDraft, TestEmailDraft
from app.services import email_configuration as service
from app.services.request_scope import resolve_workspace_id_from_request


async def scope(request: Request):
    """Resolve the authenticated graph profile and authorized workspace."""
    from app.api.errors import MissingAuthenticationError
    from app.services.permissions import get_user_node

    user = await get_user_node(resolve_principal_id(request))
    if user is None:
        raise MissingAuthenticationError("Authentication required")
    actor = user.id
    workspace = await resolve_workspace_id_from_request(request, actor)
    await service.require_admin(actor, workspace)
    return actor, workspace


async def body(request: Request, schema):
    """Validate input without reflecting credentials in errors."""
    try:
        return schema.model_validate(await request.json())
    except ValueError:
        raise BadRequestError(
            "Invalid email configuration. Check the selected fields."
        ) from None


@endpoint("/workspace/email", methods=["GET"], auth=True, tags=["Email"])
async def get_email_configuration(request: Request) -> dict:
    """Read redacted workspace provider configuration."""
    return await service.describe(*(await scope(request)))


@endpoint("/workspace/email/draft", methods=["PUT"], auth=True, tags=["Email"])
async def save_email_draft(request: Request) -> dict:
    """Save a revision-bound provider draft."""
    actor, workspace = await scope(request)
    return await service.save_draft(
        actor, workspace, await body(request, SaveEmailDraft)
    )


@endpoint("/workspace/email/test", methods=["POST"], auth=True, tags=["Email"])
async def test_email_draft(request: Request) -> dict:
    """Test the saved draft without platform fallback."""
    actor, workspace = await scope(request)
    payload = await body(request, TestEmailDraft)
    return await service.test_draft(
        actor, workspace, payload.expected_revision, requested_recipient=payload.to
    )


@endpoint("/workspace/email/activate", methods=["POST"], auth=True, tags=["Email"])
async def activate_email_configuration(request: Request) -> dict:
    """Activate only the successfully tested draft revision."""
    actor, workspace = await scope(request)
    payload = await body(request, EmailRevision)
    return await service.activate(actor, workspace, payload.expected_revision)


@endpoint("/workspace/email/disable", methods=["POST"], auth=True, tags=["Email"])
async def disable_email_configuration(request: Request) -> dict:
    """Disable custom delivery and resume platform inheritance."""
    actor, workspace = await scope(request)
    payload = await body(request, EmailRevision)
    return await service.activate(
        actor, workspace, payload.expected_revision, disable=True
    )


@endpoint("/workspace/email/history", methods=["GET"], auth=True, tags=["Email"])
async def email_delivery_history(
    request: Request, cursor: str = "", limit: int = 25
) -> dict:
    """Read bounded workspace delivery metadata."""
    from app.services.email_delivery import history

    return await history(*(await scope(request)), cursor=cursor, limit=limit)


@endpoint(
    "/workspace/email/webhooks/{connection_id}",
    methods=["POST"],
    auth=False,
    tags=["Email"],
)
async def email_provider_webhook(request: Request, connection_id: str) -> dict:
    """Verify and retain bounded provider delivery events."""
    from app.services.email_events import ingest

    # Enforce the bound while streaming, before buffering the request body.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 1_000_000:
            raise BadRequestError("Email webhook payload is too large")
    return await ingest(connection_id, bytes(raw), dict(request.headers))
