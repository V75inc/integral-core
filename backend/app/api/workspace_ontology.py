"""Workspace ontology for all authenticated users, filtered before exposure."""

import json
from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.workspace_ontology import OntologyRequest
from app.services.request_scope import resolve_workspace_id_from_request


@endpoint("/workspace-ontology", methods=["POST"], auth=True, tags=["Query"])
async def workspace_ontology(request: Request) -> Dict[str, Any]:
    """Capture one bounded neighborhood under current workspace read policy."""
    from app.services.workspace_ontology import capture_ontology

    principal_id = resolve_principal_id(request)
    if not principal_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, principal_id)
    try:
        spec = OntologyRequest.model_validate(await request.json())
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise BadRequestError(message="Invalid ontology request") from exc
    return await capture_ontology(
        principal_id=principal_id, workspace_id=workspace_id or "", spec=spec
    )


@endpoint("/workspace-ontology/roots", methods=["GET"], auth=True, tags=["Query"])
async def ontology_roots(request: Request, search: str = "") -> Dict[str, Any]:
    """Find readable Apps and Tracks without following hidden ancestors."""
    from app.services.workspace_ontology import list_ontology_roots

    principal_id = resolve_principal_id(request)
    if not principal_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, principal_id)
    if len(search) > 128:
        raise BadRequestError(message="Search must be at most 128 characters")
    roots = await list_ontology_roots(
        principal_id=principal_id, workspace_id=workspace_id or "", search=search
    )
    return {"roots": [root.model_dump() for root in roots]}
