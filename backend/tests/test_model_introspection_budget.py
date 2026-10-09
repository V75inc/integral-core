"""Discovery is bounded while full schema authoring remains lossless."""

import json

from app.agentive.tooling.bindings import _describe_operational_model_service_map
from app.services.operational_model_authoring import _model_overview


def test_agent_introspection_defaults_to_overview_but_can_request_full():
    assert _describe_operational_model_service_map({"space_id": "a"}) == {
        "app_id": "a",
        "detail": "overview",
    }
    assert _describe_operational_model_service_map(
        {"track_id": "t", "detail": "full"}
    ) == {"track_id": "t", "detail": "full"}


def test_overview_excludes_large_view_configs_and_reports_clipping():
    fields = [
        {"key": f"f{i}", "type": "text", "config": {"instructions": "x" * 10000}}
        for i in range(40)
    ]
    original = {
        "id": "model",
        "name": "Model",
        "manifest": {
            "scope": "track",
            "track": {
                "entry_types": [{"key": f"t{i}", "fields": fields} for i in range(30)],
                "views": [{"config": "z" * 100000}],
            },
        },
    }
    overview = _model_overview(original)
    shape = overview["manifest_overview"]["track"]
    assert shape["entry_type_count"] == 30
    assert len(shape["entry_types"]) == 16
    assert shape["entry_types_truncated"] is True
    assert shape["entry_types"][0]["field_count"] == 40
    assert shape["entry_types"][0]["fields_truncated"] is True
    assert shape["view_count"] == 1
    assert "manifest" not in overview
    assert len(json.dumps(overview)) < len(json.dumps(original)) / 100
    assert original["manifest"]["track"]["entry_types"][0]["fields"] == fields


def test_app_overview_does_not_expand_every_track_schema():
    result = _model_overview(
        {
            "manifest": {
                "scope": "app",
                "app": {
                    "track_templates": [
                        {
                            "key": f"t{i}",
                            "name": "Track",
                            "entry_types": [
                                {
                                    "key": "record",
                                    "fields": [{"key": "f", "config": "x" * 100000}],
                                }
                            ],
                        }
                        for i in range(50)
                    ]
                },
            }
        }
    )
    app = result["manifest_overview"]["app"]
    assert app["track_template_count"] == 50
    assert len(app["track_templates"]) == 32
    assert app["track_templates_truncated"] is True
    assert "fields" not in json.dumps(app)
