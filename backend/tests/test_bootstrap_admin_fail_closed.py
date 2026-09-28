"""Admin bootstrap must not report readiness after incomplete setup."""

from types import SimpleNamespace

import pytest

from app import bootstrap_admin
from app.api import auth
from app.config import settings
from app.services import app_graph, personal_workspace


@pytest.mark.asyncio
async def test_bootstrap_rejects_short_configured_password(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "short")

    with pytest.raises(ValueError, match="at least 12 characters"):
        await bootstrap_admin.bootstrap_admin_if_needed()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_step", ["catalog", "workspace"])
async def test_bootstrap_propagates_profile_setup_failure(monkeypatch, failed_step):
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "strong-password-123")
    existing = SimpleNamespace(
        id="auth-admin", email="admin@example.com", roles=["admin"]
    )

    async def find_user(_email):
        return existing

    async def graph_profile(_auth_user_id, _display_name):
        return SimpleNamespace(id="graph-admin")

    async def catalog(_user):
        if failed_step == "catalog":
            raise RuntimeError("catalog failed")

    async def workspace(_user):
        if failed_step == "workspace":
            raise RuntimeError("workspace failed")

    monkeypatch.setattr(
        auth,
        "_get_auth_service",
        lambda: SimpleNamespace(_find_user_by_email=find_user),
    )
    monkeypatch.setattr(bootstrap_admin, "_ensure_graph_profile", graph_profile)
    monkeypatch.setattr(app_graph, "catalog_user", catalog)
    monkeypatch.setattr(personal_workspace, "ensure_personal_workspace", workspace)

    with pytest.raises(RuntimeError, match=f"{failed_step} failed"):
        await bootstrap_admin._bootstrap_admin_once()
