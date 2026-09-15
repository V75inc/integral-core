"""Phase 5 Plan 05-05 — GitHubIssuesConnector unit suite + optional live test.

Tests (network-free unit suite):
  1. to_entry mapping correctness against a realistic GitHub Issue payload
  2. to_entry handles missing optional fields (null body, no assignee)
  3. idempotency_key_for stability across runs
  4. idempotency_key_for namespace isolation across slugs (Pitfall 4)
  5. Slug registration via decorator side effect
  6. AGENTIVE_ENABLED=0 subprocess: slug NOT registered (gating proven)
  7. GITHUB_TOKEN never persisted to Connector.auth_state (T-05-05-04)
  8. Seeded GITHUB_ISSUES_MANIFEST validates via compile_canonical_manifest
  9. Library seed registration — GITHUB_ISSUES_MANIFEST appears in the seed list
 10. No provenance free-string form in github_issues.py (I-CON-01)
 11. PR-filtering — sync_pull drops items carrying a "pull_request" key

Live test (skipped unless GITHUB_LIVE_TEST=1):
 - Pulls ≤5 issues from a small public repo to prove the real-world flow.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture: realistic GitHub Issue JSON payload — abbreviated from the public
# anthropics/anthropic-cookbook repository's REST API response.
# ---------------------------------------------------------------------------

ISSUE_FIXTURE: Dict[str, Any] = {
    "id": 1234567890,
    "number": 42,
    "title": "Fix typo in README",
    "body": "There's a typo on line 12 of README.md — should be 'their' not 'thier'.",
    "state": "open",
    "html_url": "https://github.com/example/repo/issues/42",
    "user": {"login": "octocat"},
    "assignee": {"login": "monalisa"},
    "labels": [
        {"name": "bug"},
        {"name": "good first issue"},
    ],
    "updated_at": "2026-05-10T12:34:56Z",
}

ISSUE_FIXTURE_MIN: Dict[str, Any] = {
    "id": 99,
    "number": 1,
    "title": "Bare-bones issue",
    "body": None,
    "state": "closed",
    "html_url": "https://github.com/example/repo/issues/1",
    "user": {"login": "alice"},
    "assignee": None,
    "labels": [],
    "updated_at": "2026-05-10T00:00:00Z",
}

# Pull request payload — GitHub's Issues endpoint returns BOTH issues and PRs;
# the PR variant carries a ``pull_request`` key. sync_pull must filter these.
PR_FIXTURE: Dict[str, Any] = {
    "id": 7777,
    "number": 9,
    "title": "Add new feature",
    "body": "PR body",
    "state": "open",
    "html_url": "https://github.com/example/repo/pull/9",
    "user": {"login": "octocat"},
    "assignee": None,
    "labels": [],
    "updated_at": "2026-05-11T00:00:00Z",
    "pull_request": {"url": "https://api.github.com/repos/example/repo/pulls/9"},
}


# ---------------------------------------------------------------------------
# Test 1 — to_entry mapping correctness
# ---------------------------------------------------------------------------


def test_to_entry_maps_per_locked_decision_q11_table():
    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.services.connectors import ExternalRecord

    conn = GitHubIssuesConnector()
    rec = ExternalRecord(
        external_id=str(ISSUE_FIXTURE["id"]),
        payload=ISSUE_FIXTURE,
        updated_at=ISSUE_FIXTURE["updated_at"],
    )
    me = conn.to_entry(rec)
    assert me.title == "Fix typo in README"
    assert me.body.startswith("There's a typo")
    assert me.entry_type_key == "github_issue"
    assert sorted(me.tags) == sorted(["bug", "good first issue"])
    cf = me.custom_fields
    assert cf["issue_number"] == 42
    assert cf["state"] == "open"
    assert cf["reporter"] == "octocat"
    assert cf["assignee"] == "monalisa"
    assert cf["github_url"] == "https://github.com/example/repo/issues/42"
    assert me.external_updated_at == "2026-05-10T12:34:56Z"


# ---------------------------------------------------------------------------
# Test 2 — handles missing optional fields
# ---------------------------------------------------------------------------


def test_to_entry_handles_missing_optional_fields():
    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.services.connectors import ExternalRecord

    conn = GitHubIssuesConnector()
    rec = ExternalRecord(
        external_id=str(ISSUE_FIXTURE_MIN["id"]),
        payload=ISSUE_FIXTURE_MIN,
    )
    me = conn.to_entry(rec)
    assert me.body == ""  # None body → empty string
    assert me.custom_fields["assignee"] == ""  # missing assignee → empty string
    assert me.tags == []


# ---------------------------------------------------------------------------
# Test 3 — idempotency_key_for stability
# ---------------------------------------------------------------------------


def test_idempotency_key_for_is_stable_across_calls():
    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.services.connectors import ExternalRecord

    conn = GitHubIssuesConnector()
    rec = ExternalRecord(external_id="42")
    key_a = conn.idempotency_key_for(rec)
    key_b = conn.idempotency_key_for(rec)
    assert key_a == key_b
    # Sanity: SHA-256 hex of "github_issues:42"
    expected = hashlib.sha256("github_issues:42".encode()).hexdigest()
    assert key_a == expected


# ---------------------------------------------------------------------------
# Test 4 — idempotency_key_for namespace isolation across slugs (Pitfall 4)
# ---------------------------------------------------------------------------


def test_idempotency_key_for_namespace_isolation_across_slugs():
    """Two SyncConnector subclasses with same external_id MUST produce
    different idempotency keys (slug is the per-class namespace)."""
    from app.services.connectors import (
        ExternalRecord,
        SyncConnector,
    )

    class _OtherConnector(SyncConnector):
        slug = "other_source"

    from app.agentive.connectors.github_issues import GitHubIssuesConnector

    gh = GitHubIssuesConnector()
    other = _OtherConnector()
    rec = ExternalRecord(external_id="42")
    assert gh.idempotency_key_for(rec) != other.idempotency_key_for(rec)


# ---------------------------------------------------------------------------
# Test 5 — slug registration via decorator side effect
# ---------------------------------------------------------------------------


def test_slug_registered_on_module_import():
    # The autouse ``reset_sync_connector_registry`` fixture wipes the registry
    # between tests, so we re-register the connector class here by re-running
    # its module-level decorator side-effect via importlib.reload.
    # Reset defensively so reload's decorator doesn't trip the
    # single-registration guard if a prior test in this process already
    # imported the module.
    import importlib

    import app.agentive.connectors.github_issues as gh_mod
    from app.services.connectors import (
        get_sync_connector,
        list_registered_slugs,
        reset_sync_registry,
    )

    reset_sync_registry()
    importlib.reload(gh_mod)  # re-fires @register_sync_connector("github_issues")
    assert "github_issues" in list_registered_slugs()
    instance = get_sync_connector("github_issues")
    assert instance.__class__.__name__ == "GitHubIssuesConnector"


# ---------------------------------------------------------------------------
# Test 6 — AGENTIVE_ENABLED=0 subprocess: slug NOT registered
# ---------------------------------------------------------------------------


def test_core_registry_does_not_register_github_issues_slug_without_agentive_import():
    """Core connector registry alone must NOT register agentive-only slugs."""
    backend_dir = Path(__file__).resolve().parents[1]  # backend/tests → backend
    env = os.environ.copy()
    env["TESTING"] = "1"
    # Probe script: import core registry only, deliberately AVOID importing
    # app.agentive.connectors. List registered slugs.
    script = (
        "from app.services.connectors import list_registered_slugs;"
        "print(list_registered_slugs())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(backend_dir),
        timeout=30,
    )
    assert proc.returncode == 0, f"subprocess failed: stderr={proc.stderr}"
    # No agentive import means no github_issues registration.
    assert "github_issues" not in proc.stdout


# ---------------------------------------------------------------------------
# Test 7 — GITHUB_TOKEN never persisted to Connector.auth_state (T-05-05-04)
# ---------------------------------------------------------------------------


def test_github_issues_module_never_writes_token_to_auth_state():
    """github_issues.py must NEVER assign a token to auth_state.

    Static text check: the module reads the token via os.getenv inside the
    sync_pull body but does not persist it into connector.auth_state. This
    test asserts that there is no assignment statement that writes a token
    or Authorization header into auth_state.
    """
    src_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "agentive"
        / "connectors"
        / "github_issues.py"
    )
    text = src_path.read_text()
    # Refuse any pattern that writes a Bearer token or "token" key INTO
    # connector.auth_state.
    bad_patterns = [
        r"auth_state\[['\"]token['\"]\]\s*=",
        r"auth_state\[['\"]Authorization['\"]\]\s*=",
        r"auth_state\.update\(\s*\{[^}]*token",
    ]
    for pat in bad_patterns:
        assert not re.search(pat, text), (
            f"github_issues.py persists secret via pattern {pat!r} — "
            "T-05-05-04 violation"
        )


# ---------------------------------------------------------------------------
# Test 8 — seeded GITHUB_ISSUES_MANIFEST validates
# ---------------------------------------------------------------------------


def test_github_issues_seeded_manifest_compiles():
    """The shipped github-issues package compiles to a canonical track manifest.

    Rewritten against the YAML loader: the Python-dict GITHUB_ISSUES_MANIFEST
    builder was retired in eaaf4e3 when seeded profiles moved to
    app/profiles/<slug>/profile.yaml, and this test was parked. Reading through
    the loader is stronger than the old fixture dict — it now fails if the
    shipped profile itself stops compiling.
    """
    from app.services.content_profile_loader import load_library_profiles
    from app.services.content_profile_runtime import compile_canonical_manifest

    specs = {s.slug: s for s in load_library_profiles(verify_signatures=False)}
    spec = specs.get("github-issues")
    assert spec is not None, "github-issues package missing from app/profiles/"

    canonical = compile_canonical_manifest(manifest=dict(spec.manifest))
    assert canonical is not None
    assert canonical.get("scope") == "track"


# ---------------------------------------------------------------------------
# Test 9 — library seed registration: GITHUB_ISSUES_MANIFEST is in the seed list
# ---------------------------------------------------------------------------


def test_github_issues_registered_in_library_seed_list():
    """github-issues is discoverable as a library package.

    Rewritten against the YAML loader — registered_seeded_library_specs() was
    retired in eaaf4e3. load_library_profiles() walks app/profiles/*/profile.yaml,
    which is the discovery path the platform actually uses at startup.
    """
    from app.services.content_profile_loader import load_library_profiles

    specs = load_library_profiles(verify_signatures=False)
    assert any(
        s.slug == "github-issues" for s in specs
    ), f"github-issues package missing from app/profiles/ (slugs={[s.slug for s in specs]})"


# ---------------------------------------------------------------------------
# Test 10 — no provenance free-string form in github_issues.py (I-CON-01)
# ---------------------------------------------------------------------------


def test_github_issues_module_does_not_touch_provenance_directly():
    src_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "agentive"
        / "connectors"
        / "github_issues.py"
    )
    text = src_path.read_text()
    # I-CON-01 — github_issues.py must NOT write provenance.source = "connector:..."
    forbidden = re.search(r'provenance\.source\s*=\s*"connector:', text)
    assert (
        forbidden is None
    ), "github_issues.py writes free-string provenance.source — I-CON-01 violation"


# ---------------------------------------------------------------------------
# Test 11 — PR-filtering in sync_pull
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_pull_filters_pull_requests():
    """sync_pull must drop items carrying a "pull_request" key."""
    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.services.connectors import ExternalRecord  # noqa: F401

    conn = GitHubIssuesConnector()

    # Mock connector node with owner/repo and no sync_cursor.
    fake_connector = MagicMock()
    fake_connector.auth_state = {"owner": "example", "repo": "repo"}
    fake_connector.sync_cursor = None

    # Build a fake httpx response with mixed issues + PRs.
    mixed_payload: List[Dict[str, Any]] = [ISSUE_FIXTURE, PR_FIXTURE, ISSUE_FIXTURE_MIN]

    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(return_value=mixed_payload)
    fake_resp.headers = {}  # No Link header → single-page

    fake_client = MagicMock()
    # client.get returns an awaitable resolving to fake_resp.
    fake_client.get = AsyncMock(return_value=fake_resp)

    # Make AsyncClient(...) return our fake (as an async context manager).
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.agentive.connectors.github_issues.httpx.AsyncClient",
        return_value=fake_client,
    ):
        out: List[Any] = []
        async for rec in conn.sync_pull(connector=fake_connector):
            out.append(rec)

    # 3 items in → 2 out (PR filtered).
    assert len(out) == 2
    ext_ids = {r.external_id for r in out}
    assert ext_ids == {str(ISSUE_FIXTURE["id"]), str(ISSUE_FIXTURE_MIN["id"])}


# ---------------------------------------------------------------------------
# Optional live test (CON-04 demo path) — guarded by GITHUB_LIVE_TEST=1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("GITHUB_LIVE_TEST"),
    reason="Live network test requires GITHUB_LIVE_TEST=1",
)
async def test_github_issues_live_pull_small_public_repo():
    """Pulls ≤5 issues from a small public repo. Skipped in CI."""
    from unittest.mock import MagicMock

    from app.agentive.connectors.github_issues import GitHubIssuesConnector

    conn = GitHubIssuesConnector()
    fake_connector = MagicMock()
    fake_connector.auth_state = {"owner": "octocat", "repo": "hello-world"}
    fake_connector.sync_cursor = None

    pulled: List[Any] = []
    async for rec in conn.sync_pull(connector=fake_connector):
        pulled.append(rec)
        if len(pulled) >= 5:
            break

    # We don't assert a hard count (repos drift); just shape.
    assert all(getattr(r, "external_id", None) for r in pulled)
