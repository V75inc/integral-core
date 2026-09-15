"""Phase 32 — POST /api/apps/batch-install.

Install N library bundles into the caller's active workspace in one
call. Bundles are topologically ordered by ``requires_apps[]`` so a
dependency that's also in the batch installs before its dependent.
Existing installs are skipped (idempotent). Per-bundle exceptions land
in the ``failed[]`` list — they DO NOT halt the batch.

The endpoint mirrors the convention of ``POST /api/apps`` (single
install via ``create_app_for_user``) but uses the
``services.app_lifecycle.install_app`` 12-step transaction so seeds,
hooks, settings_schema, and the ``app.installed`` ChangeEvent fire
the same way as a single-bundle install.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.app_batch_install import (
    BatchInstallRequest,
    BatchInstallResponse,
)
from app.services.request_scope import resolve_workspace_id_from_request


@endpoint(
    "/apps/batch-install",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def batch_install_apps(request: Request) -> Dict[str, Any]:
    """Install N library bundles into the caller's active workspace in one call."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    raw = await request.json() if request.method == "POST" else {}
    req = BatchInstallRequest.model_validate(raw or {})

    workspace_id = await resolve_workspace_id_from_request(request, user_id)

    # install_app expects the graph User node id (n.User.X) — the
    # OWNS edge wire + lifecycle ChangeEvent actor_id assume that
    # shape. resolve_principal_id returns the AuthUser id (o.User.X);
    # promote it to the graph User via get_user_node before dispatch.
    from app.services.permissions import get_user_node

    user_node = await get_user_node(user_id)
    actor_node_id = user_node.id if user_node else user_id

    # Deferred import — service module imports app.api.errors, which
    # triggers app/api/__init__'s endpoint loop and re-enters this
    # module. Defer to call time so the registration pass completes
    # before the service module loads.
    from app.services.app_batch_install import batch_install

    # Auto-pull each selected bundle's hard app dependencies (shared with the
    # workspace-create wizard). Settings are NOT auto-defaulted here — the
    # dialog keeps its per-app settings-finalize step, so apps that declare a
    # settings_schema still pause at ``awaiting_settings`` for user input.
    result = await batch_install(
        workspace_id=workspace_id,
        items=[item.model_dump() for item in req.items],
        actor_id=actor_node_id,
        resolve_dependencies=True,
    )
    return BatchInstallResponse.model_validate(result).model_dump()
