"""Declarative selection, profile isolation and lossless passage boundaries."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from tokenizers import Tokenizer, models, pre_tokenizers

from app.schemas.retrieve import RetrieveRequest
from app.services.retrieval.index_definition import (
    IndexDefinition,
    embedding_profile,
    selected_text,
    split_passages,
)

pytestmark = pytest.mark.smoke


def tokenizer():
    instance = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    instance.pre_tokenizer = pre_tokenizers.Whitespace()
    return instance


def test_selected_fields_exclusions_and_unknown_contract():
    entry = SimpleNamespace(
        title="A", body="B", custom_fields={"text": "C", "secret": "D", "number": 42}
    )
    declaration = IndexDefinition(exclude_fields=["body", "custom_fields.secret"])
    assert selected_text(entry, declaration) == {
        "title": "A",
        "custom_fields.text": "C",
    }
    with pytest.raises(ValidationError):
        IndexDefinition(fields=["credentials.password"])
    with pytest.raises(ValidationError):
        IndexDefinition(workspace_id="forged")


def test_explicit_scalar_metadata_is_searchable_but_exclusions_win():
    entry = SimpleNamespace(
        title="", body="", custom_fields={"count": 42, "secret": 17, "other": 99}
    )
    declaration = IndexDefinition(
        fields=[],
        metadata_fields=["custom_fields.count", "custom_fields.secret"],
        exclude_fields=["custom_fields.secret"],
    )
    assert selected_text(entry, declaration) == {"custom_fields.count": "42"}


def test_long_text_preserves_tail_offsets_and_bounds():
    profile = embedding_profile()
    text = " ".join(f"word{i}" for i in range(2000)) + " distinct-tail-marker"
    passages = split_passages({"body": text}, profile, tokenizer())
    assert len(passages) > 10
    assert passages[-1]["text"].endswith("distinct-tail-marker")
    for passage in passages:
        assert passage["text"] == text[passage["start"] : passage["end"]]
        assert len(tokenizer().encode(passage["text"]).ids) <= profile.chunk_tokens
    assert all(a["end"] >= b["start"] for a, b in zip(passages, passages[1:]))


def test_profile_changes_never_reuse_vector_space():
    original = embedding_profile()
    for modified in (
        replace(original, model="other"),
        replace(original, revision="v2"),
        replace(original, dimensions=768),
        replace(original, chunk_tokens=64),
    ):
        assert modified.id != original.id


def test_om_roundtrip_preserves_declaration():
    from app.services.operational_model_compile import (
        _normalize_entry_type_spec,
        normalize_entry_type_form_schema,
    )
    from app.services.operational_model_merge import _form_schema_from_entry_type_spec

    declaration = {
        "enabled": True,
        "fields": ["body"],
        "exclude_fields": ["custom_fields.secret"],
    }
    spec = _normalize_entry_type_spec({"name": "Note", "search_index": declaration})
    materialized = _form_schema_from_entry_type_spec(spec)
    assert materialized["search_index"]["fields"] == ["body"]
    assert (
        normalize_entry_type_form_schema(materialized)["search_index"]
        == materialized["search_index"]
    )
    with pytest.raises(Exception, match="Invalid search_index"):
        _normalize_entry_type_spec({"name": "Note", "search_index": {"bogus": True}})


def test_omitted_budgets_use_deployment_defaults(monkeypatch):
    monkeypatch.setenv("RETRIEVE_K_DEFAULT", "42")
    monkeypatch.setenv("RETRIEVE_TOP_N_DEFAULT", "4")
    request = RetrieveRequest(query="topic")
    assert (request.k, request.top_n) == (42, 4)
    assert RetrieveRequest(query="topic", k=7).k == 7


def test_registration_after_late_dsn(monkeypatch):
    from app.services import retrieval

    monkeypatch.delitem(retrieval._REGISTRY, "pgvector", raising=False)
    monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "auto")
    monkeypatch.setenv("JVSPATIAL_DB_TYPE", "postgres")
    monkeypatch.delenv("JVSPATIAL_POSTGRES_DSN", raising=False)
    assert retrieval._resolve_driver_name() == "null"
    monkeypatch.setenv("JVSPATIAL_POSTGRES_DSN", "postgresql://localhost/test")
    assert retrieval._resolve_driver_name() == "pgvector"
    monkeypatch.delitem(retrieval._REGISTRY, "pgvector")


@pytest.mark.asyncio
async def test_agent_action_requires_and_preserves_host_workspace(monkeypatch):
    import importlib

    from app.api.errors import InsufficientPermissionsError
    from app.services.agent_scope import current_scope_workspace_id

    module = importlib.import_module("app.agentive.agent_actions.retrieve_context")
    token = current_scope_workspace_id.set(None)
    try:
        with pytest.raises(InsufficientPermissionsError):
            await module.retrieve_context(agent_id="agent", query="topic")
    finally:
        current_scope_workspace_id.reset(token)
    token = current_scope_workspace_id.set("bound")
    try:
        with pytest.raises(InsufficientPermissionsError):
            await module.retrieve_context(
                agent_id="agent", query="topic", scope="workspace:foreign"
            )

        async def dispatch(subject, payload, **kwargs):
            assert payload.scope == "workspace:bound"
            return [], 0, 0, "graph", True

        monkeypatch.setattr(module, "_dispatch_retrieve_modes", dispatch)
        result = await module.retrieve_context(agent_id="agent", query="topic")
        assert result.results == [] and result.degraded
    finally:
        current_scope_workspace_id.reset(token)


def test_index_configuration_tool_discloses_the_actual_contract():
    import jsonschema

    from app.agentive.tooling import build_tool_catalogue

    tool = next(
        row
        for row in build_tool_catalogue()
        if row["name"] == "integral_configure_search_index"
    )
    schema = tool["input_schema"]
    jsonschema.validate(
        {
            "track_id": "track",
            "definition": {"fields": ["title", "custom_fields.summary"]},
        },
        schema,
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"track_id": "track", "definition": {"fields": ["credentials.password"]}},
            schema,
        )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"track_id": "track", "definition": {"workspace_id": "foreign"}}, schema
        )
