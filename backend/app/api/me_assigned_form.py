"""Authenticated member assigned-form routes (canonical substrate paths).

Legacy ``/me/onboarding-form/*`` routes remain registered in
``me_onboarding_form`` for backward compatibility.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.api import me_onboarding_form as _legacy

_ASSIGNED_FORM_TAG = "AssignedForm"


@endpoint("/me/assigned-form", methods=["GET"], auth=True, tags=[_ASSIGNED_FORM_TAG])
async def get_me_assigned_form(request: Request) -> Dict[str, Any]:
    return await _legacy.get_me_onboarding_form(request)


@endpoint("/me/assigned-form", methods=["PATCH"], auth=True, tags=[_ASSIGNED_FORM_TAG])
async def patch_me_assigned_form(request: Request) -> Dict[str, Any]:
    return await _legacy.patch_me_onboarding_form(request)


@endpoint(
    "/me/assigned-form/policies",
    methods=["GET"],
    auth=True,
    tags=[_ASSIGNED_FORM_TAG],
)
async def list_me_assigned_form_policies(request: Request) -> Dict[str, Any]:
    return await _legacy.list_me_onboarding_policies(request)


@endpoint(
    "/me/assigned-form/policies/{policy_entry_id}/document",
    methods=["GET"],
    auth=True,
    tags=[_ASSIGNED_FORM_TAG],
)
async def download_me_assigned_form_policy_document(
    request: Request, policy_entry_id: str
):
    return await _legacy.download_me_onboarding_policy_document(
        request, policy_entry_id
    )


@endpoint(
    "/me/assigned-form/attachments",
    methods=["POST"],
    auth=True,
    tags=[_ASSIGNED_FORM_TAG],
)
async def upload_me_assigned_form_attachment(request: Request) -> Dict[str, Any]:
    return await _legacy.upload_me_onboarding_attachment(request)


@endpoint(
    "/me/assigned-form/contract/generate",
    methods=["POST"],
    auth=True,
    tags=[_ASSIGNED_FORM_TAG],
)
async def generate_me_assigned_form_contract(request: Request) -> Dict[str, Any]:
    return await _legacy.generate_me_onboarding_contract(request)


@endpoint(
    "/me/assigned-form/contract",
    methods=["GET"],
    auth=True,
    tags=[_ASSIGNED_FORM_TAG],
)
async def download_me_assigned_form_contract(request: Request):
    return await _legacy.download_me_onboarding_contract(request)


@endpoint(
    "/me/assigned-form/contract/decision",
    methods=["POST"],
    auth=True,
    tags=[_ASSIGNED_FORM_TAG],
)
async def decide_me_assigned_form_contract(request: Request) -> Dict[str, Any]:
    return await _legacy.decide_me_onboarding_contract(request)
