"""public_share.extensions survive compile when public sharing is disabled."""

from app.services.operational_model_compile import _normalize_public_share_spec


def test_disabled_public_share_keeps_onboarding_extensions():
    block = {
        "enabled": False,
        "permissions": {"update_entries": False},
        "extensions": {
            "contract_review_document_type": "employment_contract",
            "policy_catalog_track_type_key": "onboarding_documents",
        },
    }
    normalized = _normalize_public_share_spec(block, where="app.tracks[x].public_share")
    assert normalized is not None
    assert normalized.get("enabled") is False
    assert (
        normalized.get("extensions", {}).get("contract_review_document_type")
        == "employment_contract"
    )


def test_disabled_public_share_without_extensions_is_omitted():
    block = {"enabled": False, "permissions": {"update_entries": False}}
    assert _normalize_public_share_spec(block, where="x") is None
