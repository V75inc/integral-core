"""Phase 5 Plan 05-05 — GitHub Issues reference SyncConnector (CON-04).

Mirror-model: GitHub repository Issues as Integral Entries via the pull-only
``SyncConnector`` ABC (Plan 05-01 + Plan 05-03 sync runtime). One demo proof
that the framework works end-to-end against a real external system.

Locked decisions (CONTEXT.md):
  #13 — Authentication: anonymous read for public repos (60 req/h GitHub
        rate limit) by default; optional ``GITHUB_TOKEN`` env var raises to
        5000 req/h. Token loaded fresh from env on every ``sync_pull`` —
        NEVER persisted to ``Connector.auth_state`` (T-05-05-04 mitigation:
        credential leak via ChangeEvent before/after snapshot).
  §Q11 — Mapping table: GitHub Issue → ``github_issue`` Entry (see ``to_entry``).

Invariants this module preserves:
  I-CON-01 — does NOT touch ``provenance`` directly; sync_runtime writes the
             split-shape Provenance object. This module ONLY exposes the
             handoff ``MaterializedEntry``.
  I-CON-03 — registers via ``@register_sync_connector("github_issues")``;
             the single-registration guard prevents slug collisions.
  D-08 / I-CON-05 — lives in ``agentive/`` and is only imported when
             ``AGENTIVE_ENABLED=1`` via the package's ``__init__``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from app.services.connectors import (
    ConflictPolicy,
    ExternalRecord,
    MaterializedEntry,
    SyncConnector,
    register_sync_connector,
)

logger = logging.getLogger(__name__)


def _parse_next_link(link_header: str) -> Optional[str]:
    """Parse the GitHub ``Link`` header → next-page URL (or None).

    Header shape (RFC 5988):
        <https://api.github.com/repositories/123/issues?page=2>; rel="next",
        <https://api.github.com/repositories/123/issues?page=5>; rel="last"
    """
    if not link_header:
        return None
    for part in link_header.split(","):
        segments = part.strip().split(";")
        if len(segments) < 2:
            continue
        url = segments[0].strip().strip("<>")
        for seg in segments[1:]:
            if seg.strip() == 'rel="next"':
                return url
    return None


@register_sync_connector("github_issues")
class GitHubIssuesConnector(SyncConnector):
    """Pull-only GitHub Issues sync.

    Materializes Issues as ``github_issue`` Entries on the bound Track. PRs
    are filtered out (the GitHub Issues endpoint returns both at the same URL;
    PR variants carry a ``pull_request`` key that we drop).
    """

    slug: str = "github_issues"
    conflict_policy: ConflictPolicy = "last_write_wins"

    # Safety cap: 100 issues/page * 10 pages = 1000 issues per pull.
    # Prevents a runaway sync against a very large repo from blocking the loop.
    _MAX_PAGES: int = 10

    async def sync_pull(self, *, connector: Any) -> AsyncIterator[ExternalRecord]:
        """Yield Issues from the configured (owner, repo). Cursor via ``?since``.

        Requires ``connector.auth_state`` to carry ``owner`` and ``repo``
        keys identifying the GitHub repository to mirror. Returns silently
        on missing config rather than raising — the sync_runtime emits
        ``connector.sync.complete`` with zero stats in that case (operator
        notices the no-op via logs).

        Auth note (locked decision #13 + T-05-05-04): the optional
        ``GITHUB_TOKEN`` env var is read fresh on every call. It is attached
        to the request headers only — it is NEVER assigned to
        ``connector.auth_state`` and therefore can never leak into a
        ChangeEvent before/after snapshot.
        """
        auth_state: Dict[str, Any] = getattr(connector, "auth_state", None) or {}
        owner = auth_state.get("owner") or ""
        repo = auth_state.get("repo") or ""
        if not owner or not repo:
            logger.warning(
                "github_issues sync: missing owner/repo in connector.auth_state — "
                "aborting (connector=%s)",
                getattr(connector, "id", "<unknown>"),
            )
            return

        token = os.getenv("GITHUB_TOKEN")  # ephemeral; NEVER persisted
        headers: Dict[str, str] = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        params: Dict[str, Any] = {"state": "all", "per_page": 100}
        if getattr(connector, "sync_cursor", None):
            params["since"] = connector.sync_cursor

        url: Optional[str] = f"https://api.github.com/repos/{owner}/{repo}/issues"
        pages = 0
        async with httpx.AsyncClient(timeout=30.0) as client:
            while url and pages < self._MAX_PAGES:
                # First page uses the initial params; subsequent pages walk
                # the Link header URL which already encodes pagination state.
                resp = await client.get(
                    url, headers=headers, params=params if pages == 0 else None
                )
                resp.raise_for_status()
                issues = resp.json() or []
                for issue in issues:
                    # GitHub returns Pull Requests at this endpoint; filter them.
                    # PR variants carry a "pull_request" key (object).
                    if "pull_request" in issue:
                        continue
                    yield ExternalRecord(
                        external_id=str(issue.get("id", "")),
                        payload=issue,
                        updated_at=issue.get("updated_at"),
                    )
                url = _parse_next_link(resp.headers.get("Link", ""))
                pages += 1

    def to_entry(self, record: ExternalRecord) -> MaterializedEntry:
        """Map one GitHub Issue → MaterializedEntry per locked decision §Q11.

        Mapping table (§Q11):
            Issue.id            → external_id              (stringified upstream)
            Issue.title         → Entry.title
            Issue.body          → Entry.body                (null → empty string)
            Issue.labels[*].name→ Entry.tags
            Issue.number        → custom_fields.issue_number
            Issue.state         → custom_fields.state
            Issue.user.login    → custom_fields.reporter
            Issue.assignee.login→ custom_fields.assignee    (missing → empty string)
            Issue.html_url      → custom_fields.github_url
            entry_type_key      = "github_issue"
        """
        issue: Dict[str, Any] = record.payload or {}
        labels: List[Dict[str, Any]] = issue.get("labels", []) or []
        user = issue.get("user") or {}
        assignee = issue.get("assignee") or {}
        return MaterializedEntry(
            title=issue.get("title") or "",
            body=issue.get("body") or "",
            entry_type_key="github_issue",
            tags=[lbl.get("name", "") for lbl in labels if lbl.get("name")],
            custom_fields={
                "issue_number": issue.get("number"),
                "state": issue.get("state") or "",
                "reporter": (user.get("login") or "") if isinstance(user, dict) else "",
                "assignee": (
                    (assignee.get("login") or "") if isinstance(assignee, dict) else ""
                ),
                "github_url": issue.get("html_url") or "",
            },
            external_updated_at=issue.get("updated_at"),
        )
