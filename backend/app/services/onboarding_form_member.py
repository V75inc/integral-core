"""Resolve an employee's onboarding form for the signed-in workspace member."""

from __future__ import annotations

from typing import Any, List, Optional, Set, Tuple

from app.models.edges import HAS_MEMBER_REF
from app.models.nodes import App, Entry, EntryType, Track, User
from app.services.operational_model_compile import slug_manifest_key


def _as_single_id(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0] or "").strip() if value else ""
    return str(value or "").strip()


def _entry_type_key(entry_type: Optional[EntryType]) -> str:
    if entry_type is None:
        return ""
    manifest_key = str(
        (getattr(entry_type, "form_schema", None) or {}).get("_manifest_entry_type_key")
        or ""
    ).strip()
    return slug_manifest_key(manifest_key or str(getattr(entry_type, "name", "") or ""))


async def _entry_manifest_key(entry: Entry) -> str:
    if not entry.type_id:
        return ""
    et = await EntryType.get(entry.type_id)
    return _entry_type_key(et)


def _member_link_ids(user: User) -> Set[str]:
    ids = {
        str(getattr(user, "id", "") or "").strip(),
        str(getattr(user, "user_id", "") or "").strip(),
    }
    return {item for item in ids if item}


def member_field_matches_user(stored_member: Any, user: User) -> bool:
    """True when employee.member references this user (graph or auth id)."""
    stored = _as_single_id(stored_member)
    return bool(stored) and stored in _member_link_ids(user)


async def _resolve_graph_user(member_user_id: str) -> Optional[User]:
    from app.services.permissions import get_user_node

    if not str(member_user_id or "").strip():
        return None
    return await get_user_node(str(member_user_id).strip())


async def _workspace_ids_to_scan(user: User, preferred_workspace_id: str) -> List[str]:
    from app.services.workspace_permissions import list_accessible_workspaces

    ordered: List[str] = []
    seen: Set[str] = set()
    pref = str(preferred_workspace_id or "").strip()
    if pref:
        ordered.append(pref)
        seen.add(pref)
    for ws in await list_accessible_workspaces(str(user.id)):
        ws_id = str(getattr(ws, "id", "") or "").strip()
        if ws_id and ws_id not in seen:
            ordered.append(ws_id)
            seen.add(ws_id)
    return ordered


async def _effective_track_workspace_id(track: Track) -> str:
    ws = str(getattr(track, "workspace_id", "") or "").strip()
    if ws:
        return ws
    try:
        apps = await track.nodes(
            edge=["CONTAINS"], direction="in", node=["App"], limit=1
        )
        if apps:
            return str(getattr(apps[0], "workspace_id", "") or "").strip()
    except Exception:
        pass
    return ""


async def _tracks_in_workspace(workspace_id: str) -> List[Track]:
    if not workspace_id:
        return []
    by_id: dict[str, Track] = {}
    for row in await Track.find({"workspace_id": workspace_id}) or []:
        by_id[str(row.id)] = row
    for app in await App.find({"workspace_id": workspace_id}) or []:
        try:
            contained = await app.nodes(edge=["CONTAINS"], node=["Track"], limit=200)
        except Exception:
            contained = []
        for track in contained or []:
            by_id[str(track.id)] = track
    return list(by_id.values())


def _is_employees_track(track: Track) -> bool:
    title_slug = slug_manifest_key(str(getattr(track, "title", "") or ""))
    template_slug = slug_manifest_key(str(getattr(track, "template_id", "") or ""))
    if title_slug in ("employees", "employee") or template_slug in (
        "employees",
        "employee",
    ):
        return True
    return title_slug.endswith("_employees") or title_slug.startswith("employees_")


def _is_onboarding_track(track: Track) -> bool:
    title_slug = slug_manifest_key(str(getattr(track, "title", "") or ""))
    template_slug = slug_manifest_key(str(getattr(track, "template_id", "") or ""))
    if title_slug == "employee_onboarding" or template_slug == "employee_onboarding":
        return True
    return "onboarding" in title_slug and (
        "employee" in title_slug or title_slug == "onboarding"
    )


async def _employee_linked_to_user(employee: Entry, user: User) -> bool:
    cf = dict(getattr(employee, "custom_fields", None) or {})
    if member_field_matches_user(cf.get("member"), user):
        return True
    try:
        linked_users = await employee.nodes(
            edge=[HAS_MEMBER_REF], direction="out", node=["User"], limit=8
        )
    except Exception:
        linked_users = []
    link_ids = _member_link_ids(user)
    for linked in linked_users or []:
        if str(getattr(linked, "id", "") or "") in link_ids:
            return True
        auth_id = str(getattr(linked, "user_id", "") or "")
        if auth_id and auth_id in link_ids:
            return True
    return False


async def _find_employee_via_member_edges(user: User) -> Optional[Entry]:
    try:
        linked_entries = await user.nodes(
            edge=[HAS_MEMBER_REF], direction="in", node=["Entry"], limit=100
        )
    except Exception:
        linked_entries = []
    for entry in linked_entries or []:
        if await _entry_manifest_key(entry) == "employee":
            return entry
    return None


async def find_employee_for_member(
    *, workspace_id: str, member_user_id: str, user: Optional[User] = None
) -> Optional[Entry]:
    """Employee entry whose ``member`` field points at the signed-in user."""
    if user is None:
        user = await _resolve_graph_user(member_user_id)
    if user is None:
        return None

    via_edge = await _find_employee_via_member_edges(user)
    if via_edge is not None:
        return via_edge

    for ws_id in await _workspace_ids_to_scan(user, workspace_id):
        for track in await _tracks_in_workspace(ws_id):
            if not _is_employees_track(track):
                continue
            entries = await Entry.find({"track_id": track.id})
            for row in entries or []:
                if await _employee_linked_to_user(row, user):
                    return row
    return None


async def find_onboarding_form_for_employee(
    *,
    workspace_id: str,
    employee_id: str,
    user: User,
) -> Optional[Tuple[Entry, Track]]:
    if not employee_id:
        return None
    scan_workspace_ids = await _workspace_ids_to_scan(user, workspace_id)

    for ws_id in scan_workspace_ids:
        for track in await _tracks_in_workspace(ws_id):
            if not _is_onboarding_track(track):
                continue
            entries = await Entry.find({"track_id": track.id})
            for row in entries or []:
                cf = dict(getattr(row, "custom_fields", None) or {})
                if _as_single_id(cf.get("employee")) != employee_id:
                    continue
                if await _entry_manifest_key(row) != "onboarding_form":
                    continue
                return row, track
    return None


async def _resolve_explicit_form_entry(
    *,
    form_entry_id: str,
    user: User,
) -> Tuple[Entry, Track, Entry]:
    from app.services.workspace_permissions import user_in_workspace_member_pool

    form = await Entry.get(form_entry_id)
    if form is None:
        raise LookupError("Onboarding form not found")
    track = await Track.get(form.track_id)
    if track is None:
        raise LookupError("Onboarding form not found")
    track_ws = await _effective_track_workspace_id(track)
    if not track_ws or not await user_in_workspace_member_pool(str(user.id), track_ws):
        raise LookupError("Onboarding form not found")

    if await _entry_manifest_key(form) != "onboarding_form":
        raise LookupError("Not an onboarding form")

    employee_id = _as_single_id((form.custom_fields or {}).get("employee"))
    if not employee_id:
        raise LookupError("Onboarding form is not linked to an employee")
    employee = await Entry.get(employee_id)
    if employee is None:
        raise LookupError("Employee record for this form was not found")
    if not await _employee_linked_to_user(employee, user):
        raise PermissionError("You can only access your own onboarding form")
    return form, track, employee


async def resolve_member_onboarding_form(
    *, workspace_id: str, member_user_id: str, form_entry_id: Optional[str] = None
) -> Tuple[Entry, Track, Entry]:
    """Return (form_entry, track, employee) for the signed-in member."""
    user = await _resolve_graph_user(member_user_id)
    if user is None:
        raise LookupError("No employee record is linked to your account")

    if form_entry_id:
        return await _resolve_explicit_form_entry(
            form_entry_id=str(form_entry_id).strip(), user=user
        )

    employee = await find_employee_for_member(
        workspace_id=workspace_id, member_user_id=member_user_id, user=user
    )
    if employee is None:
        raise LookupError("No employee record is linked to your account")

    resolved = await find_onboarding_form_for_employee(
        workspace_id=workspace_id,
        employee_id=str(employee.id),
        user=user,
    )
    if resolved is None:
        raise LookupError("No onboarding form found for your employee record")
    form, track = resolved
    return form, track, employee


def onboarding_form_locked(form: Entry) -> bool:
    status = str((form.custom_fields or {}).get("status") or "draft").strip().lower()
    return status == "approved"


async def member_owns_onboarding_form(
    *, member_user_id: str, form: Entry, employee: Entry
) -> bool:
    user = await _resolve_graph_user(member_user_id)
    if user is None:
        cf = dict(getattr(employee, "custom_fields", None) or {})
        return _as_single_id(cf.get("member")) == str(member_user_id)
    return await _employee_linked_to_user(employee, user)
