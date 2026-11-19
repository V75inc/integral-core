"""Standalone Core exposes document primitives without a bundle-specific store."""

import importlib.util


def test_core_package_omits_retired_document_host_implementations():
    assert (
        importlib.util.find_spec("app.services.documents.entry_template_store") is None
    )
    assert importlib.util.find_spec("app.services.esign.contract_decision") is None
    assert importlib.util.find_spec("app.services.esign.dispatch") is None
    assert importlib.util.find_spec("app.services.onboarding_prompt") is None
    assert importlib.util.find_spec("app.services.documents.render") is not None
