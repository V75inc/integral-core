"""Permission-checked destinations for completed changes, never proposed targets."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from app.models.edges import CONTAINS
from app.models.nodes import App, Dashboard, Entry, Track
from app.services.notification_paths import entry_path
from app.services.permissions import resolve_role
from app.services.resource_location import DATA_APP_STATES, locate_resource
from app.services.staging_consumed_nav import extract_consumed_nav

logger = logging.getLogger(__name__)


def app_section_path(app_id: str, section: str, dashboard_id: str = "") -> str:
    """Address an App tab explicitly, independent of remembered browser state."""
    path = f"/apps/{quote(app_id, safe='')}?section={section}"
    if dashboard_id:
        path += f"&dashboard_id={quote(dashboard_id, safe='')}"
    return path


def completion_refs(
    result: dict[str, Any], kind: str, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    """Extract successful effect references without trusting supplied routes."""
    if (
        result.get("error")
        or result.get("ok") is False
        or result.get("filed") is False
        or result.get("skipped")
        or result.get("needs_agent_build")
        or kind.startswith("delete_")
    ):
        return []
    if kind in {"bulk_update_entries", "bulk_move_entries"}:
        ids = arguments.get("entry_ids") or []
        completed = (
            result.get("updated")
            if kind == "bulk_update_entries"
            else result.get("moved_count")
        )
        if not isinstance(ids, list) or completed != len(ids):
            return []
        return [
            {"kind": "entry", "id": identifier}
            for identifier in ids
            if isinstance(identifier, str)
        ]
    refs = result.get("resources") if kind in {"verify_build", "completed"} else None
    if not isinstance(refs, list):
        nav = extract_consumed_nav(result, {"kind": kind, "diff_machine": arguments})
        refs = nav.get("created") or []
        if not refs:
            for resource_kind, key in (
                ("entry", "entry_id"),
                ("track", "track_id"),
                ("app", "app_id"),
            ):
                if arguments.get(key):
                    refs = [{"kind": resource_kind, "id": arguments[key]}]
                    break
    # Explicit Home changes take precedence over a single-Track App shortcut.
    if kind == "update_app" and "home" in arguments:
        refs = [{"kind": "home", "id": arguments.get("app_id")}]
    return refs


async def completion_links(
    result: dict[str, Any],
    *,
    kind: str,
    arguments: dict[str, Any],
    user_id: str,
    workspace_id: str,
) -> list[dict[str, str]]:
    """Resolve actual rooted resources; supplied ids never grant link visibility."""
    if kind in {"routine_task_create", "routine_task_update", "routine_task_cancel"}:
        if (
            result.get("error")
            or result.get("ok") is False
            or result.get("skipped")
            or result.get("needs_agent_build")
            or result.get("filed") is False
        ):
            return []
        from app.agentive.services.routine_tasks import get_routine_task
        from app.api.errors import ResourceNotFoundError

        identifier = result.get("id")
        if not isinstance(identifier, str) or not identifier:
            return []
        try:
            routine = await get_routine_task(user_id=user_id, routine_id=identifier)
        except ResourceNotFoundError:
            return []
        except Exception:
            # Navigation is optional after a completed effect. Withhold the
            # link on a read outage rather than misreporting a saved schedule.
            logger.warning("Scheduled routine navigation unavailable", exc_info=True)
            return []
        if routine.workspace_id != workspace_id:
            return []
        return [
            {
                "kind": "routine",
                "id": routine.id,
                "label": "scheduled routine",
                "url": f"/background-tasks?routine={quote(routine.id, safe='')}#scheduled-routines",
            }
        ]
    refs = completion_refs(result, kind, arguments)
    candidates = [
        r for r in refs if isinstance(r, dict) and isinstance(r.get("id"), str)
    ]
    # Count the whole result before deciding whether an Entry is singular.
    entry_ids = {r["id"] for r in candidates if r.get("kind") == "entry"}
    has_app = any(r.get("kind") == "app" for r in candidates)
    if has_app:
        candidates = [
            r for r in candidates if r.get("kind") in {"app", "dashboard", "home"}
        ]
    links: dict[str, dict[str, str]] = {}

    async def target(resource_kind: str, identifier: str):
        location = await locate_resource(
            resource_kind, identifier, allowed_app_states=DATA_APP_STATES
        )
        if location is None or location.workspace_id != workspace_id:
            return None
        if (
            await resolve_role(
                user_id,
                "app" if resource_kind == "dashboard" else resource_kind,
                location.app_id if resource_kind == "dashboard" else identifier,
            )
            is None
        ):
            return None
        return location

    for ref in candidates[:100]:
        resource_kind, identifier = ref.get("kind"), ref["id"]
        source_url = (
            f"/apps/{quote(identifier, safe='')}" if resource_kind == "app" else ""
        )
        auth_kind = "app" if resource_kind == "home" else resource_kind
        if auth_kind not in {"app", "track", "entry", "dashboard"}:
            continue
        location = await target(auth_kind, identifier)
        if location is None:
            continue
        if resource_kind == "entry" and len(entry_ids) != 1:
            resource_kind, identifier = "track", location.track_id
            if not identifier or await target("track", identifier) is None:
                continue
        if resource_kind == "app":
            app = await App.get(identifier)
            # Two actual neighbours suffice to distinguish exactly one from many.
            tracks = (
                await app.nodes(
                    edge=[CONTAINS], node=["Track"], direction="out", limit=2
                )
                if app
                else []
            )
            if len(tracks) == 1 and await target("track", tracks[0].id) is not None:
                resource_kind, identifier = "track", tracks[0].id
        if resource_kind in {"app", "home"}:
            node = await App.get(identifier)
            label = (node.name if node else "") or "App"
            url = f"/apps/{quote(identifier, safe='')}"
            if resource_kind == "home":
                label += " Home"
                url = app_section_path(identifier, "home")
        elif resource_kind == "dashboard":
            node = await Dashboard.get(identifier)
            label = (node.name if node else "") or "Dashboard"
            url = app_section_path(location.app_id, "dashboards", identifier)
        elif resource_kind == "track":
            node = await Track.get(identifier)
            label = (node.title if node else "") or "Track"
            url = f"/tracks/{quote(identifier, safe='')}"
        else:
            node = await Entry.get(identifier)
            label = (node.title if node else "") or "Entry"
            url = (
                entry_path(identifier, location.track_id)
                if location.track_id
                else f"/entries/{quote(identifier, safe='')}"
            )
        if node is not None:
            links[url] = {
                "kind": str(resource_kind),
                "id": identifier,
                "label": label,
                "url": url,
            }
            if source_url and resource_kind == "track":
                links[url]["source_url"] = source_url
            if (
                resource_kind == "entry"
                and location.track_id
                and await target("track", location.track_id) is not None
            ):
                parent = await Track.get(location.track_id)
                if parent:
                    links[url].update(
                        parent_id=parent.id,
                        parent_url=f"/tracks/{quote(parent.id, safe='')}",
                        parent_label=parent.title or "Track",
                    )
    return list(links.values())


def collapse_completion_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    """Collapse an approval episode without adding awaits to queue mutation windows."""
    entry_ids = {link.get("id") for link in links if link.get("kind") == "entry"}
    selected = {}
    for link in links:
        if link.get("kind") == "entry" and len(entry_ids) > 1:
            if not link.get("parent_url"):
                continue
            link = {
                "kind": "track",
                "id": link["parent_id"],
                "url": link["parent_url"],
                "label": link["parent_label"],
            }
        if link.get("url"):
            selected[link["url"]] = link
    return list(selected.values())


def append_completion_links(
    output: str, links: list[dict[str, str]], affected_entry_ids: set[str] | None = None
) -> str:
    """Keep model prose and add any missing canonical destinations once."""
    redirects = {
        link["source_url"]: link["url"] for link in links if link.get("source_url")
    }

    def normalize(match):
        label, url = match.group(1), match.group(2)
        parsed = urlsplit(url)
        entry_id = parse_qs(parsed.query).get("entry", [""])[0]
        if parsed.path.startswith("/entries/"):
            entry_id = unquote(parsed.path.removeprefix("/entries/"))
        if (
            affected_entry_ids
            and len(affected_entry_ids) > 1
            and entry_id in affected_entry_ids
        ):
            return label
        return f"[{label}]({redirects.get(url, url)})"

    output = re.sub(r"\[([^\]]+)\]\((/[^)\s]+)\)", normalize, output)
    missing = []
    for link in links:
        url = link.get("url", "")
        if (
            not url.startswith(
                ("/apps/", "/tracks/", "/entries/", "/background-tasks?routine=")
            )
            or f"]({url})" in output
        ):
            continue
        label = re.sub(r"([\\`*{}\[\]()<>_])", r"\\\1", link.get("label") or "Open")
        missing.append(f"[{label}]({url})")
    return output + ("\n\n" + " · ".join(missing) if missing else "")
