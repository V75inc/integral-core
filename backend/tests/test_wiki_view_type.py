"""Wiki view type — registry and config normalization."""

import pytest

from app.api.errors import BadRequestError
from app.services import operational_model_runtime as runtime
from app.views import operational_model_view_types as view_types


def test_wiki_registered_in_view_type_registry():
    spec = view_types.get("wiki")
    assert spec is not None
    assert spec.type == "wiki"
    assert spec.label == "Wiki"
    assert "parent_field" in spec.config_schema


def test_normalize_wiki_view_config_requires_parent_field():
    with pytest.raises(BadRequestError) as exc:
        runtime.normalize_view_config("wiki", {})
    assert "parent_field" in str(exc.value.message).lower()


def test_normalize_wiki_view_config_defaults_body_and_title():
    cfg = runtime.normalize_view_config(
        "wiki",
        {"parent_field": "parent"},
    )
    assert cfg["parent_field"] == "parent"
    assert cfg["body_field"] == "body"
    assert cfg["title_field"] == "title"


def test_materialize_wiki_view_from_manifest_spec():
    cfg = runtime.materialize_view_config_from_spec(
        {
            "key": "docs",
            "name": "Docs",
            "view_type": "wiki",
            "parent_field": "parent",
            "body_field": "body",
        }
    )
    assert cfg["parent_field"] == "parent"
    assert cfg["body_field"] == "body"


@pytest.mark.asyncio
async def test_describe_substrate_includes_wiki(test_user):
    from app.agentive.tooling.dispatch import dispatch_tool

    if test_user is None:
        pytest.skip("no test_user node available")
    result = await dispatch_tool(
        "integral_describe_substrate",
        {},
        principal_id=test_user.user_id,
        scope=None,
    )
    assert result.is_error is False, result.message
    view_keys = {v["type"] for v in result.data["view_types"]}
    assert "wiki" in view_keys
