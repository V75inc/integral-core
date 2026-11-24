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
  I-CON-05 — registered by importing this package. The agentive layer
             is always loaded.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, Optional

import httpx

from app.services.connectors import (
    ConflictPolicy,
    ExternalRecord,
    MaterializedEntry,
    SyncConnector,
    register_sync_connector,
)

logger = logging.getLogger(__name__)


def _cursor_field(query: Optional[dict]) -> Optional[str]:
    """Return the query key whose value is a cursor template."""
    if not isinstance(query, dict):
        return None
    for key, value in query.items():
        if isinstance(value, str) and "{{cursor}}" in "".join(value.split()):
            return str(key)
    return None


def _github_issue_params(
    manifest: Optional[dict], cursor: Optional[str]
) -> Dict[str, str]:
    """Build the issue query from the package. The cursor value is still supplied here."""
    from app.connectors.package_runtime import package_action_request, render

    def query_of(source: Optional[dict]) -> Optional[dict]:
        if not isinstance(source, dict):
            return None
        query = source.get("query")
        return query if isinstance(query, dict) and query else None

    request = package_action_request("github_issues", "list_issues", manifest)
    query = query_of(request)
    if query is None and isinstance(manifest, dict):
        query = query_of(package_action_request("github_issues", "list_issues", None))
    if query is None:
        raise ValueError("GitHub package does not declare an issue query")
    cursor_key = _cursor_field(query)
    if cursor_key is None and isinstance(manifest, dict):
        cursor_key = _cursor_field(
            query_of(package_action_request("github_issues", "list_issues", None))
        )
    if not cursor_key:
        raise ValueError("GitHub package does not declare a cursor field")
    rendered = render(query, {}, {"cursor": ""})
    params = {
        str(key): "" if value is None else str(value)
        for key, value in rendered.items()
        if str(key) != cursor_key
    }
    if cursor:
        params[cursor_key] = str(cursor)
    return params


def _github_max_pages(manifest: Optional[dict] = None) -> int:
    """Return the page cap from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def pages(source: Optional[dict]) -> Optional[int]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "sync" not in capabilities:
            return None
        sync = capabilities.get("sync")
        if not isinstance(sync, dict) or "max_pages" not in sync:
            return None
        raw = sync.get("max_pages")
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            return None
        return raw

    found = pages(manifest)
    if found is None:
        found = pages(admitted_manifest("github_issues") or {})
    if found is None:
        raise ValueError("GitHub package does not declare a page cap")
    return found


def _github_issue_select(manifest: Optional[dict] = None) -> str:
    """Return the issue-list selector from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def select_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("select"):
                return str(output["select"])
            return None
        return None

    found = select_of(manifest)
    if found is None:
        found = select_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare an issue selector")
    return found


def _github_issue_id(manifest: Optional[dict] = None) -> str:
    """Return the issue id path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def id_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("id"):
                return str(output["id"])
            return None
        return None

    found = id_of(manifest)
    if found is None:
        found = id_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare an issue id")
    return found


def _github_issue_updated_at(manifest: Optional[dict] = None) -> str:
    """Return the issue timestamp path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def updated_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("updated_at"):
                return str(output["updated_at"])
            return None
        return None

    found = updated_of(manifest)
    if found is None:
        found = updated_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare an issue timestamp")
    return found


def _github_next_header(manifest: Optional[dict] = None) -> str:
    """Return the next-page header from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def header_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("next_header"):
                return str(output["next_header"])
            return None
        return None

    found = header_of(manifest)
    if found is None:
        found = header_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare a next page header")
    return found


def _github_next_rel(manifest: Optional[dict] = None) -> str:
    """Return the next-page relation from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def rel_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("next_rel"):
                return str(output["next_rel"])
            return None
        return None

    found = rel_of(manifest)
    if found is None:
        found = rel_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare a next relation")
    return found


def _github_exclude_key(manifest: Optional[dict] = None) -> str:
    """Return the skipped-record key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("exclude"):
                return str(output["exclude"])
            return None
        return None

    found = key_of(manifest)
    if found is None:
        found = key_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare an excluded record key")
    return found


def _github_issue_title(manifest: Optional[dict] = None) -> str:
    """Return the issue title path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def title_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("title"):
                return str(output["title"])
            return None
        return None

    found = title_of(manifest)
    if found is None:
        found = title_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare an issue title")
    return found


def _github_issue_body(manifest: Optional[dict] = None) -> str:
    """Return the issue body path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def body_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("body"):
                return str(output["body"])
            return None
        return None

    found = body_of(manifest)
    if found is None:
        found = body_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare an issue body")
    return found


def _github_issue_status(manifest: Optional[dict] = None) -> str:
    """Return the issue status path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def status_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("status"):
                return str(output["status"])
            return None
        return None

    found = status_of(manifest)
    if found is None:
        found = status_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare an issue status")
    return found


def _github_issue_number(manifest: Optional[dict] = None) -> str:
    """Return the issue number path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def number_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("number"):
                return str(output["number"])
            return None
        return None

    found = number_of(manifest)
    if found is None:
        found = number_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare an issue number")
    return found


def _github_issue_url(manifest: Optional[dict] = None) -> str:
    """Return the issue link path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def url_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("url"):
                return str(output["url"])
            return None
        return None

    found = url_of(manifest)
    if found is None:
        found = url_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare an issue link")
    return found


def _github_issue_actor(manifest: Optional[dict] = None) -> str:
    """Return the reporter path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def actor_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("actor"):
                return str(output["actor"])
            return None
        return None

    found = actor_of(manifest)
    if found is None:
        found = actor_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare a reporter")
    return found


def _github_issue_assignee(manifest: Optional[dict] = None) -> str:
    """Return the assignee path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def assignee_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("assignee"):
                return str(output["assignee"])
            return None
        return None

    found = assignee_of(manifest)
    if found is None:
        found = assignee_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare an assignee")
    return found


def _github_issue_tags(manifest: Optional[dict] = None) -> str:
    """Return the tag path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def tags_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_issues":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("tags"):
                return str(output["tags"])
            return None
        return None

    found = tags_of(manifest)
    if found is None:
        found = tags_of(admitted_manifest("github_issues") or {})
    if not found:
        raise ValueError("GitHub package does not declare issue tags")
    return found


def _github_issue_headers(manifest: Optional[dict], token: str) -> Dict[str, str]:
    """Build request headers from the package. The token is still supplied here."""
    from app.connectors.package_runtime import package_action_request, render

    def declared(source: Optional[dict]) -> Optional[dict]:
        if not isinstance(source, dict) or "headers" not in source:
            return None
        headers = source.get("headers")
        return headers if isinstance(headers, dict) else None

    headers = declared(package_action_request("github_issues", "list_issues", manifest))
    if headers is None and isinstance(manifest, dict):
        headers = declared(package_action_request("github_issues", "list_issues", None))
    if not isinstance(headers, dict) or "Accept" not in headers:
        raise ValueError("GitHub package does not declare an issue Accept header")
    rendered = render(headers, {}, {})
    out = {
        str(key): "" if value is None else str(value) for key, value in rendered.items()
    }
    if token:
        out["Authorization"] = f"Bearer {token}"
    return out


def _parse_next_link(link_header: str, relation: str) -> Optional[str]:
    """Parse a ``Link`` header for the declared next-page relation."""
    if not link_header or not relation:
        return None
    marker = f'rel="{relation}"'
    for part in link_header.split(","):
        segments = part.strip().split(";")
        if len(segments) < 2:
            continue
        url = segments[0].strip().strip("<>")
        for seg in segments[1:]:
            if seg.strip() == marker:
                return url
    return None


@register_sync_connector("github_issues")
class GitHubIssuesConnector(SyncConnector):
    """Pull-only GitHub Issues sync.

    Materializes Issues as ``github_issue`` Entries on the bound Track. Rows
    that carry the package exclude key are dropped.
    """

    slug: str = "github_issues"

    @property
    def conflict_policy(self) -> ConflictPolicy:  # type: ignore[override]
        """Read the conflict policy from the admitted package."""
        from app.connectors.package_runtime import package_conflict_policy

        return package_conflict_policy(None, self.slug)  # type: ignore[return-value]

    async def sync_pull(self, *, connector: Any) -> AsyncIterator[ExternalRecord]:
        """Yield Issues from the configured (owner, repo). Cursor via ``?since``.

        Requires ``connector.auth_state`` to carry ``owner`` and ``repo``
        keys identifying the GitHub repository to mirror. Returns silently
        on missing config rather than raising — the sync_runtime emits
        ``connector.sync.complete`` with zero stats in that case (operator
        notices the no-op via logs).

        Auth note (locked decision #13 + T-05-05-04): the operator credential
        named by the package is read fresh on every call. It is attached to
        the request headers only — it is NEVER assigned to
        ``connector.auth_state`` and therefore can never leak into a
        ChangeEvent before/after snapshot.
        """
        from app.agentive.services.connector_registry_node import decrypt_auth_state
        from app.connectors.package_runtime import admitted_manifest, deployment_setting

        auth_state: Dict[str, Any] = decrypt_auth_state(
            getattr(connector, "auth_state", None) or {}
        )
        connector.auth_state = auth_state
        package_config = getattr(connector, "package_config", None)
        config = package_config if isinstance(package_config, dict) else {}
        owner = auth_state.get("owner") or config.get("owner") or ""
        repo = auth_state.get("repo") or config.get("repo") or ""
        if not owner or not repo:
            logger.warning(
                "github_issues sync: missing owner/repo in connector.auth_state — "
                "aborting (connector=%s)",
                getattr(connector, "id", "<unknown>"),
            )
            return

        stored = getattr(connector, "package_manifest", None)
        manifest = (
            stored
            if isinstance(stored, dict) and stored.get("capabilities")
            else admitted_manifest(self.slug) or {}
        )
        package_auth = manifest.get("auth") or {}
        env_name = ""
        if package_auth.get("type") == "operator":
            env_name = str(package_auth.get("env") or "")
        token = deployment_setting([env_name]) if env_name else ""
        from app.connectors.package_runtime import (
            follow_package_link,
            package_action_request,
            package_http,
            perform_request,
            select_output,
        )

        request = package_action_request(self.slug, "list_issues", manifest)
        if not isinstance(request, dict) or not request.get("path"):
            raise ValueError("GitHub package does not declare list_issues")
        sent = dict(request)
        sent_headers = dict(sent.get("headers") or {})
        if token:
            sent_headers["Authorization"] = f"Bearer {token}"
        sent["headers"] = sent_headers
        host = {
            "http": package_http(self.slug, manifest),
            "auth": {"type": "none"},
        }
        pages = 0
        max_pages = _github_max_pages(manifest)
        selector = _github_issue_select(manifest)
        id_path = _github_issue_id(manifest)
        updated_path = _github_issue_updated_at(manifest)
        next_header = _github_next_header(manifest)
        next_rel = _github_next_rel(manifest)
        exclude_key = _github_exclude_key(manifest)
        cursor = str(getattr(connector, "sync_cursor", None) or "")
        async with httpx.AsyncClient(timeout=30.0) as client:
            next_url: Optional[str] = None
            while pages < max_pages:
                if next_url is None:
                    body, resp_headers = await perform_request(
                        host,
                        sent,
                        {"owner": owner, "repo": repo},
                        {},
                        {"cursor": cursor},
                        client,
                        True,
                    )
                else:
                    body, resp_headers = await follow_package_link(
                        self.slug, manifest, next_url, sent_headers
                    )
                selected = select_output([] if body is None else body, selector)
                issues = selected if isinstance(selected, list) else []
                for issue in issues:
                    if not isinstance(issue, dict) or exclude_key in issue:
                        continue
                    found_id = select_output(issue, id_path)
                    found_at = select_output(issue, updated_path)
                    yield ExternalRecord(
                        external_id="" if found_id is None else str(found_id),
                        payload=issue,
                        updated_at=None if found_at is None else str(found_at),
                    )
                next_url = _parse_next_link(
                    str(resp_headers.get(next_header, "") or ""), next_rel
                )
                pages += 1
                if not next_url:
                    break

    def to_entry(
        self, record: ExternalRecord, manifest: Optional[dict] = None
    ) -> MaterializedEntry:
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
        from app.connectors.package_runtime import (
            package_field_name,
            package_record_type,
            select_output,
        )

        issue: Dict[str, Any] = record.payload or {}
        found_title = select_output(issue, _github_issue_title(manifest))
        title = "" if found_title is None else str(found_title)
        found_body = select_output(issue, _github_issue_body(manifest))
        body = "" if found_body is None else str(found_body)
        found_status = select_output(issue, _github_issue_status(manifest))
        status = "" if found_status is None else str(found_status)
        issue_number = select_output(issue, _github_issue_number(manifest))
        found_url = select_output(issue, _github_issue_url(manifest))
        issue_url = "" if found_url is None else str(found_url)
        found_actor = select_output(issue, _github_issue_actor(manifest))
        reporter = "" if found_actor is None else str(found_actor)
        found_assignee = select_output(issue, _github_issue_assignee(manifest))
        assignee_name = "" if found_assignee is None else str(found_assignee)
        found_tags = select_output(issue, _github_issue_tags(manifest))
        tags = (
            [str(tag) for tag in found_tags if tag]
            if isinstance(found_tags, list)
            else []
        )
        found_updated = select_output(issue, _github_issue_updated_at(manifest))
        return MaterializedEntry(
            title=title,
            body=body,
            entry_type_key=package_record_type(manifest, self.slug),
            tags=tags,
            custom_fields={
                package_field_name(manifest, self.slug, "number"): issue_number,
                package_field_name(manifest, self.slug, "status"): status,
                package_field_name(manifest, self.slug, "actor"): reporter,
                package_field_name(manifest, self.slug, "assignee"): assignee_name,
                package_field_name(manifest, self.slug, "url"): issue_url,
            },
            external_updated_at=(None if found_updated is None else str(found_updated)),
        )
