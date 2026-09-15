"""GET /api/apps/{slug}/skills — generic skill catalogue per bundle.

Phase 31 (DR-31-01 §8): the Resident persona was split across two
bundles — ``sales`` (pre-sales drafting: scope_from_transcript +
proposal_from_scope) and ``portfolio`` (case_study_from_project).
"""

import pytest


@pytest.mark.asyncio
async def test_apps_skills_sales_returns_two_sales_skills(
    authenticated_client, test_user
):
    # No workspace install needed — endpoint reads compiled manifest by slug.
    resp = await authenticated_client.get("/api/apps/sales/skills")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    keys = {s["key"] for s in body["skills"]}
    # Sales bundle declares the two pre-sales drafting skills (DR-31-01 §8).
    assert "scope_from_transcript" in keys
    assert "proposal_from_scope" in keys
    # The portfolio-owned case-study skill must NOT appear under sales.
    assert "case_study_from_project" not in keys


@pytest.mark.asyncio
async def test_apps_skills_portfolio_returns_case_study_skill(
    authenticated_client, test_user
):
    resp = await authenticated_client.get("/api/apps/portfolio/skills")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    keys = {s["key"] for s in body["skills"]}
    # Portfolio bundle declares the case-study drafting skill (DR-31-01 §8).
    assert "case_study_from_project" in keys
    # Pre-sales drafting skills live under the sales bundle, not portfolio.
    assert "scope_from_transcript" not in keys
    assert "proposal_from_scope" not in keys


@pytest.mark.asyncio
async def test_unknown_slug_returns_404(authenticated_client):
    resp = await authenticated_client.get("/api/apps/no-such-bundle/skills")
    assert resp.status_code == 404, resp.text
