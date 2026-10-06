"""Publication review is computed by Core and bound to both schema snapshots."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.staging_executors import _x_publish_profile_draft
from app.models.nodes import OperationalModel
from app.services import operational_model_authoring as authoring
from app.services.operational_model_diff import format_publication_review


@pytest.mark.asyncio
async def test_changed_draft_refuses_publish_before_any_effect(monkeypatch):
    parent = SimpleNamespace(id="parent", manifest={"entry_types": []})
    draft = SimpleNamespace(
        id="draft", status="draft", draft_of_id="parent", manifest={"entry_types": []}
    )
    fingerprint = authoring.publication_review_fingerprint(draft, parent)
    draft.manifest = {"entry_types": [{"key": "loan", "name": "Loan"}]}
    monkeypatch.setattr(OperationalModel, "get", AsyncMock(side_effect=[draft, parent]))
    monkeypatch.setattr(authoring, "_user_can_edit_cp", AsyncMock(return_value=True))
    publish = AsyncMock()
    monkeypatch.setattr(
        "app.services.operational_model_atomic_swap.publish_draft", publish
    )
    result = await authoring.publish_draft_for_agent(
        user_id="owner", draft_id="draft", expected_review_fingerprint=fingerprint
    )
    assert result["error"] == "review_stale"
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreviewed_legacy_proposal_requires_new_review():
    result = await _x_publish_profile_draft("owner", {"draft_id": "draft"})
    assert result["error"] == "review_required"


def test_review_renders_actual_field_choices_and_record_impact():
    review = {
        "diff": {
            "entry_types": {
                "changed": [
                    {
                        "key": "Loan",
                        "fields": {
                            "added": [
                                {
                                    "key": "condition",
                                    "name": "Condition",
                                    "type": "select",
                                    "choices": ["Good", "Worn", "Damaged"],
                                }
                            ]
                        },
                    }
                ]
            }
        },
        "entry_impact": [
            {"total": 3, "would_fail_validation": 0, "would_need_migration": 0}
        ],
    }
    rendered = format_publication_review(review)
    assert "Added field **Condition**" in rendered
    assert all(choice in rendered for choice in ("Good", "Worn", "Damaged"))
    assert "Records inspected: **3**" in rendered


def test_changed_parent_invalidates_review_fingerprint():
    parent = SimpleNamespace(id="parent", manifest={"entry_types": []})
    draft = SimpleNamespace(id="draft", manifest={"entry_types": []})
    fingerprint = authoring.publication_review_fingerprint(draft, parent)
    parent.manifest = {"entry_types": [{"key": "new_type"}]}
    assert authoring.publication_review_fingerprint(draft, parent) != fingerprint


def test_singleton_settings_diff_is_rendered_without_list_assumptions():
    rendered = format_publication_review(
        {
            "diff": {
                "app_settings": {
                    "changed": True,
                    "before": {},
                    "after": {"title": "Loans"},
                }
            }
        }
    )
    assert "Changed **app settings**" in rendered
    assert '"title": "Loans"' in rendered
