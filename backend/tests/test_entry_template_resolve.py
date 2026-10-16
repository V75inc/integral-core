"""Document template resolution — default pick + document_type-only generate."""

from __future__ import annotations

from app.services.documents.entry_template_store import (
    TemplateView,
    _pick_default_active_template,
)


def test_pick_default_active_template_prefers_is_default() -> None:
    rows = [
        TemplateView(
            id="a",
            name="A",
            module="finance",
            document_type="invoice",
            context_type="",
            is_default=False,
        ),
        TemplateView(
            id="b",
            name="B",
            module="human-resources",
            document_type="employment_contract",
            context_type="",
            is_default=True,
        ),
    ]
    picked = _pick_default_active_template(rows)
    assert picked is not None
    assert picked.id == "b"


def test_pick_default_active_template_falls_back_to_first() -> None:
    rows = [
        TemplateView(
            id="only",
            name="Only",
            module="payroll",
            document_type="payslip",
            context_type="",
        ),
    ]
    assert _pick_default_active_template(rows).id == "only"
    assert _pick_default_active_template([]) is None


from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("installed", ["document-templates", "document_templates"])
@pytest.mark.parametrize("declared", ["document-templates", "document_templates"])
async def test_provider_lookup_matches_canonical_and_legacy_slugs(
    monkeypatch, installed, declared
):
    from app.services.documents import entry_template_store as store

    app = SimpleNamespace(
        id="app",
        workspace_id="workspace",
        installed_package_slug=installed,
        lifecycle_state="active",
    )
    monkeypatch.setattr(store.App, "find", AsyncMock(return_value=[app]))
    assert (
        await store.find_entry_template_provider_app(
            "workspace", template_provider_slug=declared
        )
        is app
    )


@pytest.mark.asyncio
async def test_provider_lookup_does_not_fallback_to_an_unrelated_app(monkeypatch):
    from app.services.documents import entry_template_store as store

    app = SimpleNamespace(
        id="app", installed_package_slug="other-provider", lifecycle_state="active"
    )
    monkeypatch.setattr(store.App, "find", AsyncMock(return_value=[app]))
    assert (
        await store.find_entry_template_provider_app(
            "workspace", template_provider_slug="document-templates"
        )
        is None
    )
    assert await store.find_entry_template_provider_app("workspace") is None


@pytest.mark.asyncio
async def test_generate_threads_declared_provider_to_template_listing(monkeypatch):
    from app.services.documents import entry_template_store as store

    template = store.TemplateView(
        id="template",
        name="Neutral template",
        module="",
        document_type="document",
        context_type="",
        current_version_id="version",
        status="active",
    )
    listing = AsyncMock(return_value=[template])
    monkeypatch.setattr(store, "list_template_views", listing)
    monkeypatch.setattr(
        store,
        "get_version_view",
        AsyncMock(
            return_value=store.VersionView(
                id="version",
                template_id="template",
                workspace_id="workspace",
                status="published",
            )
        ),
    )
    actual, _ = await store.resolve_for_generate(
        workspace_id="workspace",
        document_type="document",
        template_provider_slug="my-template-library",
    )
    assert actual is template
    assert listing.call_args.kwargs["template_provider_slug"] == "my-template-library"
