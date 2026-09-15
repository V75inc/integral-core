"""Hook schemas — transform/public_share/precompute/tool_call."""

import pytest
from pydantic import ValidationError


def test_transform_request_minimal():
    from app.schemas.hooks.transform import TransformRequest

    req = TransformRequest(to_track="n.Track.x")
    assert req.to_track == "n.Track.x"
    assert req.hook_key is None
    assert req.override is False


def test_transform_response_shape():
    from app.schemas.hooks.transform import TransformResponse

    r = TransformResponse(new_entry_id="n.Entry.y", hook_key="proposal_to_opportunity")
    assert r.new_entry_id == "n.Entry.y"


def test_public_share_mint_request_default_role_viewer():
    from app.schemas.hooks.public_share import MintPublicShareRequest

    r = MintPublicShareRequest()
    assert r.expires_at is None


def test_shared_response_carries_projection():
    from app.schemas.hooks.public_share import PublicShareReadResponse

    r = PublicShareReadResponse(
        hook_key="case_study_share",
        projection={"title": "x"},
        shared_at="2026-01-01T00:00:00Z",
    )
    assert r.projection["title"] == "x"


def test_precompute_request_requires_hook_key():
    from app.schemas.hooks.precompute import PrecomputeRequest

    req = PrecomputeRequest(hook_key="proposal_pricing")
    assert req.hook_key == "proposal_pricing"
    with pytest.raises(ValidationError):
        PrecomputeRequest()  # hook_key required


def test_tool_call_request_input_is_dict():
    from app.schemas.hooks.tool_call import ToolCallRequest

    req = ToolCallRequest(input={"a": 1})
    assert req.input == {"a": 1}
