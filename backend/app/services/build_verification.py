"""Build verification (W1.5): read the approved revision back, do not rebuild it.

``integral_verify_build`` checks the exact approved blueprint against the
objects recorded on the execution receipt. Dashboards, routines, seeds,
skills, and the relation form the design did not choose may be absent.
A denied or failed read is never reported as a missing object or as verified.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from jvspatial.api.exceptions import InsufficientPermissionsError

from app.services.permissions import resolve_role


class ReadDenied(Exception):
    """The caller cannot read this object."""


class ReadFailed(Exception):
    """The read did not complete."""


def receipt_id(*, design_id: str, design_revision: int, batch_token: str) -> str:
    """Stable id for one apply of one design revision."""
    raw = f"{design_id}\n{design_revision}\n{batch_token}".encode()
    return "xr." + hashlib.sha256(raw).hexdigest()[:32]


def _fold(value: Any) -> str:
    return str(value or "").strip().casefold()


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _fold(value)).strip("_")


def _label(node: Dict[str, Any]) -> str:
    return str(node.get("name") or node.get("title") or node.get("instruction") or "")


def _nodes_from_result(kind: str, result: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """Objects one applied batch step created, as ``(kind, node)``."""
    if not isinstance(result, dict) or result.get("error"):
        return []
    found: List[Tuple[str, Dict[str, Any]]] = []
    for key in (
        "app",
        "track",
        "entry",
        "tag",
        "view",
        "dashboard",
        "skill",
        "routine",
    ):
        node = result.get(key)
        if isinstance(node, dict) and (node.get("id") or node.get("key")):
            found.append((key, node))
    for tag in result.get("tags_created") or []:
        if isinstance(tag, dict) and tag.get("id"):
            found.append(("tag", tag))
    template = result.get("track_template")
    if isinstance(template, dict) and (template.get("key") or template.get("name")):
        found.append(("track_template", template))
    if kind == "save_view" and result.get("view_id"):
        found.append(
            ("view", {"id": result["view_id"], "name": result.get("name") or ""})
        )
    if kind == "author_skill" and result.get("skill_id"):
        found.append(
            (
                "skill",
                {
                    "id": result["skill_id"],
                    "key": result.get("key") or "",
                    "name": result.get("name") or "",
                },
            )
        )
    if kind == "routine_task_create" and result.get("id"):
        found.append(("routine", result))
    if kind == "create_dashboard" and result.get("id") and not found:
        found.append(("dashboard", result))
    if kind in {"create_app", "create_track", "create_entry"} and result.get("id"):
        singular = kind.removeprefix("create_")
        if not any(item_kind == singular for item_kind, _node in found):
            found.append((singular, result))
    return found


def _take(
    pool: Dict[str, List[Dict[str, Any]]], kind: str, name: str
) -> Optional[Dict[str, Any]]:
    rows = pool.get(kind) or []
    want = _fold(name)
    for index, node in enumerate(rows):
        if _fold(_label(node)) == want or (
            kind == "skill" and node.get("key") == _slug(name)
        ):
            return rows.pop(index)
    if kind == "routine":
        for index, node in enumerate(rows):
            if want and want in _fold(node.get("instruction")):
                return rows.pop(index)
    if len(rows) == 1 and kind in {"app", "dashboard", "routine", "skill"}:
        return rows.pop(0)
    return None


def build_item_mapping(
    blueprint: Dict[str, Any], execute_result: Any
) -> Dict[str, Any]:
    """Map blueprint item ids to the objects this apply created.

    Fields, entry types, and tag groups point at their Track. Views, seeds,
    skills, routines, and the dashboard point at their own ids.
    """
    results = execute_result
    if isinstance(execute_result, dict):
        results = execute_result.get("results") or []
    pool: Dict[str, List[Dict[str, Any]]] = {}
    for item in results or []:
        if not isinstance(item, dict):
            continue
        for kind, node in _nodes_from_result(
            str(item.get("kind") or ""), item.get("result")
        ):
            pool.setdefault(kind, []).append(node)

    mapping: Dict[str, Any] = {}
    app = blueprint.get("app") or {}
    app_node = _take(pool, "app", str(app.get("name") or ""))
    app_id = str((app_node or {}).get("id") or "")
    if app.get("id") and app_id:
        mapping[app["id"]] = {"kind": "app", "object_id": app_id}

    track_ids: Dict[str, str] = {}
    for track in blueprint.get("tracks") or []:
        node = _take(pool, "track", str(track.get("name") or ""))
        track_object = str((node or {}).get("id") or "")
        if not track.get("id") or not track_object:
            continue
        track_ids[track["id"]] = track_object
        mapping[track["id"]] = {"kind": "track", "object_id": track_object}
        _map_track_children(mapping, track, track_object)

    for template in blueprint.get("track_templates") or []:
        node = _take(pool, "track_template", str(template.get("name") or ""))
        key = str((node or {}).get("key") or _slug(template.get("name")))
        object_id = str((node or {}).get("app_id") or app_id)
        if not template.get("id") or not object_id or not node:
            continue
        mapping[template["id"]] = {
            "kind": "track_template",
            "object_id": object_id,
            "template_key": key,
        }
        for entry_type in template.get("entry_types") or []:
            if entry_type.get("id"):
                mapping[entry_type["id"]] = {
                    "kind": "template_entry_type",
                    "object_id": object_id,
                    "template_key": key,
                    "name": entry_type.get("name") or "",
                }
            for field in entry_type.get("fields") or []:
                if field.get("id"):
                    mapping[field["id"]] = {
                        "kind": "template_field",
                        "object_id": object_id,
                        "template_key": key,
                        "entry_type": entry_type.get("name") or "",
                        "field_key": field.get("key") or "",
                    }

    for view in blueprint.get("views") or []:
        node = _take(pool, "view", str(view.get("name") or ""))
        view_id = str((node or {}).get("id") or "")
        track_object = track_ids.get(str(view.get("track") or ""))
        if view.get("id") and view_id and track_object:
            mapping[view["id"]] = {
                "kind": "view",
                "object_id": view_id,
                "track_id": track_object,
            }

    for seed in blueprint.get("seeds") or []:
        node = _take(pool, "entry", str(seed.get("title") or ""))
        entry_id = str((node or {}).get("id") or "")
        if seed.get("id") and entry_id:
            mapping[seed["id"]] = {"kind": "seed", "object_id": entry_id}

    dashboard = blueprint.get("dashboard")
    if isinstance(dashboard, dict) and dashboard.get("id"):
        node = _take(pool, "dashboard", str(dashboard.get("name") or ""))
        dash_id = str((node or {}).get("id") or "")
        if dash_id and app_id:
            mapping[dashboard["id"]] = {
                "kind": "dashboard",
                "object_id": dash_id,
                "app_id": app_id,
            }
            for widget in dashboard.get("widgets") or []:
                if widget.get("id"):
                    mapping[widget["id"]] = {
                        "kind": "widget",
                        "object_id": dash_id,
                        "app_id": app_id,
                        "title": widget.get("title") or "",
                    }

    for skill in blueprint.get("skills") or []:
        node = _take(pool, "skill", str(skill.get("name") or ""))
        skill_id = str((node or {}).get("id") or "")
        if skill.get("id") and skill_id and app_id:
            mapping[skill["id"]] = {
                "kind": "skill",
                "object_id": skill_id,
                "app_id": app_id,
            }

    for routine in blueprint.get("routines") or []:
        node = _take(pool, "routine", str(routine.get("name") or ""))
        routine_id = str((node or {}).get("id") or "")
        if routine.get("id") and routine_id:
            mapping[routine["id"]] = {"kind": "routine", "object_id": routine_id}

    return mapping


def _map_track_children(
    mapping: Dict[str, Any], track: Dict[str, Any], track_object: str
) -> None:
    for entry_type in track.get("entry_types") or []:
        if entry_type.get("id"):
            mapping[entry_type["id"]] = {
                "kind": "entry_type",
                "object_id": track_object,
                "name": entry_type.get("name") or "",
            }
        for field in entry_type.get("fields") or []:
            if field.get("id"):
                mapping[field["id"]] = {
                    "kind": "field",
                    "object_id": track_object,
                    "entry_type": entry_type.get("name") or "",
                    "field_key": field.get("key") or "",
                }
    for group in track.get("tag_groups") or []:
        if group.get("id"):
            mapping[group["id"]] = {
                "kind": "tag_group",
                "object_id": track_object,
                "names": [str(tag) for tag in group.get("tags") or []],
            }


def make_execution_receipt(
    *,
    design_id: str,
    design_revision: int,
    blueprint_digest: str,
    blueprint: Optional[Dict[str, Any]],
    batch_token: str,
    execute_result: Any,
    applied_at: str,
    user_turn: int,
) -> Dict[str, Any]:
    """The receipt stored on the design: revision, batch, and item mapping."""
    return {
        "id": receipt_id(
            design_id=design_id,
            design_revision=design_revision,
            batch_token=batch_token,
        ),
        "batch_token": batch_token,
        "design_id": design_id,
        "design_revision": design_revision,
        "blueprint_digest": blueprint_digest,
        "mapping": build_item_mapping(blueprint or {}, execute_result),
        "applied_at": applied_at,
        "user_turn": user_turn,
    }


def _option_names(raw: Any) -> List[str]:
    names = []
    for item in raw or []:
        if isinstance(item, str):
            names.append(_fold(item))
        elif isinstance(item, dict):
            names.append(
                _fold(item.get("value") or item.get("label") or item.get("name"))
            )
    return [name for name in names if name]


def _relation_matches(expected: Optional[Dict[str, Any]], live: Any) -> bool:
    """The form the design chose is present. The other form is not required."""
    if not expected:
        return not isinstance(live, dict) or not live.get("target")
    if not isinstance(live, dict):
        return False
    if str(live.get("target") or "") != str(expected.get("target") or ""):
        return False
    if expected.get("target") == "track":
        got = str(live.get("target_track_template") or live.get("track_template") or "")
        want = str(expected.get("target_track_template") or "")
        return bool(got) and got in {want, _slug(want)}
    return not live.get("target_track_template")


def _field_matches(expected: Dict[str, Any], live: Optional[Dict[str, Any]]) -> bool:
    if not live:
        return False
    if _fold(live.get("type")) != _fold(expected.get("type")):
        return False
    if not _relation_matches(expected.get("relation"), live.get("relation")):
        return False
    wanted = _option_names(expected.get("options"))
    if wanted and not set(wanted) <= set(_option_names(live.get("options"))):
        return False
    return True


def _constituents(blueprint: Dict[str, Any]) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Required constituents, plus optional ones the design actually promised."""
    rows: List[Tuple[str, str, Dict[str, Any]]] = []
    app = blueprint.get("app") or {}
    if app.get("id"):
        rows.append((app["id"], "app", app))
    for section in ("tracks", "track_templates"):
        for track in blueprint.get(section) or []:
            kind = "track" if section == "tracks" else "track_template"
            if track.get("id"):
                rows.append((track["id"], kind, track))
            for entry_type in track.get("entry_types") or []:
                et_kind = "entry_type" if section == "tracks" else "template_entry_type"
                if entry_type.get("id"):
                    rows.append((entry_type["id"], et_kind, entry_type))
                field_kind = "field" if section == "tracks" else "template_field"
                for field in entry_type.get("fields") or []:
                    if field.get("id"):
                        rows.append((field["id"], field_kind, field))
            for group in track.get("tag_groups") or []:
                if group.get("id"):
                    rows.append((group["id"], "tag_group", group))
    for view in blueprint.get("views") or []:
        if view.get("id"):
            rows.append((view["id"], "view", view))
    dashboard = blueprint.get("dashboard")
    if isinstance(dashboard, dict) and dashboard.get("id"):
        rows.append((dashboard["id"], "dashboard", dashboard))
        for widget in dashboard.get("widgets") or []:
            if widget.get("id"):
                rows.append((widget["id"], "widget", widget))
    for key in ("skills", "routines", "seeds"):
        for item in blueprint.get(key) or []:
            if isinstance(item, dict) and item.get("id"):
                rows.append(
                    (item["id"], key.rstrip("s") if key != "seeds" else "seed", item)
                )
    for item in blueprint.get("platform_defaults") or []:
        if isinstance(item, dict) and item.get("id"):
            rows.append((item["id"], "platform_default", item))
    return rows


def _matches(kind: str, expected: Dict[str, Any], snap: Dict[str, Any]) -> bool:
    if kind == "app":
        return _fold(snap.get("name")) == _fold(expected.get("name"))
    if kind == "track":
        return _fold(snap.get("name")) == _fold(expected.get("name"))
    if kind in {"entry_type", "template_entry_type"}:
        return _fold(snap.get("name")) == _fold(expected.get("name"))
    if kind in {"field", "template_field"}:
        return _field_matches(expected, snap)
    if kind == "tag_group":
        live = {_fold(name) for name in snap.get("names") or []}
        return {_fold(name) for name in expected.get("tags") or []} <= live
    if kind == "view":
        if _fold(snap.get("name")) != _fold(expected.get("name")):
            return False
        if _fold(snap.get("type")) != _fold(expected.get("type")):
            return False
        if expected.get("is_default") and not snap.get("is_default"):
            return False
        return True
    if kind == "seed":
        if _fold(snap.get("title")) != _fold(expected.get("title")):
            return False
        live_fields = snap.get("fields") or {}
        return all(
            live_fields.get(key) not in ("", None)
            for key in expected.get("fields") or {}
        )
    if kind == "dashboard":
        return _fold(snap.get("name")) == _fold(expected.get("name"))
    if kind == "widget":
        titles = {_fold(title) for title in snap.get("titles") or []}
        return _fold(expected.get("title")) in titles
    if kind == "skill":
        visibility = expected.get("visibility") or "app_private"
        if visibility == "workspace":
            return snap.get("private") is False
        return snap.get("private") is True and snap.get("app_id") == snap.get(
            "authorized_app_id"
        )
    if kind == "routine":
        if expected.get("cron"):
            return str(snap.get("cron") or "") == str(expected.get("cron"))
        return True
    if kind == "track_template":
        return _fold(snap.get("name")) == _fold(expected.get("name"))
    if kind == "platform_default":
        detail = _fold(expected.get("detail"))
        if "feed" in detail or _fold(expected.get("kind")) == "feed":
            return set(snap.get("track_ids") or []) <= set(
                snap.get("feed_track_ids") or []
            )
        return False
    return False


def _overall(statuses: List[str]) -> str:
    if any(status == "read_failed" for status in statuses):
        return "failed"
    if any(status == "denied" for status in statuses):
        return "blocked"
    if any(status in {"missing", "mismatch"} for status in statuses):
        return "partial"
    return "verified"


async def verify_loaded(
    *,
    blueprint: Dict[str, Any],
    design_id: str,
    design_revision: int,
    receipt: Dict[str, Any],
    reader: Any,
) -> Dict[str, Any]:
    """Judge one already-loaded design. ``reader.read`` is the only I/O."""
    mapping = receipt.get("mapping") if isinstance(receipt.get("mapping"), dict) else {}
    items: List[Dict[str, Any]] = []
    track_ids = [
        spec["object_id"]
        for spec in mapping.values()
        if isinstance(spec, dict)
        and spec.get("kind") == "track"
        and spec.get("object_id")
    ]
    app_spec = mapping.get((blueprint.get("app") or {}).get("id"))
    app_id = app_spec.get("object_id") if isinstance(app_spec, dict) else ""

    for item_id, kind, expected in _constituents(blueprint):
        if kind == "platform_default":
            wants_feed = (
                "feed" in _fold(expected.get("detail"))
                or _fold(expected.get("kind")) == "feed"
            )
            if not wants_feed:
                items.append(
                    {
                        "id": item_id,
                        "status": "mismatch",
                        "detail": "this platform default has no machine check",
                    }
                )
                continue
            if not track_ids:
                items.append(
                    {
                        "id": item_id,
                        "status": "missing",
                        "detail": "the build recorded no object",
                    }
                )
                continue
            locator = {
                "kind": "platform_default",
                "track_ids": track_ids,
                "expect": "feed",
            }
        else:
            locator = mapping.get(item_id)
        if not locator:
            items.append(
                {
                    "id": item_id,
                    "status": "missing",
                    "detail": "the build recorded no object",
                }
            )
            continue
        if kind == "skill" and isinstance(locator, dict):
            locator = {**locator, "authorized_app_id": app_id}
        try:
            snap = await reader.read(locator)
        except ReadDenied:
            items.append({"id": item_id, "status": "denied"})
            continue
        except ReadFailed:
            items.append({"id": item_id, "status": "read_failed"})
            continue
        except (
            Exception
        ):  # noqa: BLE001 — a reader bug is a failed read, not a missing object
            items.append({"id": item_id, "status": "read_failed"})
            continue
        if snap is None:
            items.append(
                {
                    "id": item_id,
                    "status": "missing",
                    "detail": "the recorded object is gone",
                }
            )
            continue
        if kind == "skill" and isinstance(snap, dict):
            snap = {**snap, "authorized_app_id": app_id}
        if _matches(kind, expected, snap):
            items.append({"id": item_id, "status": "present"})
        elif kind == "platform_default":
            items.append(
                {
                    "id": item_id,
                    "status": "missing",
                    "detail": "Feed is not on every Track",
                }
            )
        else:
            items.append({"id": item_id, "status": "mismatch"})

    statuses = [item["status"] for item in items]
    return {
        "design_id": design_id,
        "design_revision": design_revision,
        "execution_receipt_id": receipt.get("id"),
        "status": _overall(statuses),
        "items": items,
    }


def _revision(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _find_design(user_id: str, design_id: str) -> Tuple[Any, Dict[str, Any]]:
    from app.services.chat_threads import list_threads

    for thread in await list_threads(user_id=user_id, include_archived=True):
        marker = getattr(thread, "design_proposed", None) or {}
        if isinstance(marker, dict) and marker.get("design_id") == design_id:
            if getattr(thread, "user_id", None) not in ("", user_id):
                continue
            return thread, marker
    return None, {}


async def verify_build(
    user_id: str,
    design_id: str,
    design_revision: Any,
    execution_receipt_id: str,
) -> Dict[str, Any]:
    """Read tool. Rejects a different revision or a receipt from another apply.

    Never stages or applies a build. A second call reads the objects again.
    """
    revision = _revision(design_revision)
    if not str(design_id or "").strip() or revision is None:
        return {
            "error": "invalid_verification_request",
            "detail": "design_id and design_revision are required.",
        }
    _thread, marker = await _find_design(user_id, str(design_id).strip())
    if not marker:
        return {
            "error": "design_not_found",
            "detail": "No design with that id is on a conversation you own.",
        }
    blueprint = marker.get("blueprint")
    if not isinstance(blueprint, dict):
        return {
            "error": "blueprint_required",
            "detail": "This design has no typed blueprint to verify.",
        }
    stored_revision = _revision(marker.get("blueprint_revision"))
    if stored_revision != revision:
        return {
            "error": "revision_mismatch",
            "detail": (
                f"The design is at revision {stored_revision}. "
                "Verify that revision; an older one is not the approved build."
            ),
        }
    receipt = marker.get("build_receipt")
    if (
        not isinstance(receipt, dict)
        or receipt.get("id") != execution_receipt_id
        or receipt.get("design_id") != design_id
        or _revision(receipt.get("design_revision")) != revision
    ):
        return {
            "error": "receipt_mismatch",
            "detail": (
                "That execution receipt is not the apply recorded for this "
                "design revision. Do not build again; verify the receipt from "
                "the apply that just finished, or apply the current revision first."
            ),
        }
    return await verify_loaded(
        blueprint=blueprint,
        design_id=str(design_id),
        design_revision=revision,
        receipt=receipt,
        reader=_Reader(user_id),
    )


class _Reader:
    """Independent reads of the objects named on the receipt."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self._entry_type_cache: Dict[str, List[Any]] = {}
        self._templates: Dict[str, Dict[str, Any]] = {}
        self._tag_cache: Dict[str, List[str]] = {}
        self._view_cache: Dict[str, List[Any]] = {}

    async def read(self, locator: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        kind = locator.get("kind")
        try:
            if kind == "app":
                return await self._app(str(locator.get("object_id") or ""))
            if kind == "track":
                return await self._track(str(locator.get("object_id") or ""))
            if kind == "entry_type":
                return await self._entry_type(locator)
            if kind == "field":
                return await self._field(locator)
            if kind == "tag_group":
                names = await self._tag_names(str(locator.get("object_id") or ""))
                return {"names": names}
            if kind == "view":
                return await self._view(locator)
            if kind == "seed":
                return await self._seed(str(locator.get("object_id") or ""))
            if kind in {"dashboard", "widget"}:
                return await self._dashboard(locator)
            if kind == "skill":
                return await self._skill(locator)
            if kind == "routine":
                return await self._routine(str(locator.get("object_id") or ""))
            if kind in {"track_template", "template_entry_type", "template_field"}:
                return await self._template_part(locator)
            if kind == "platform_default":
                return await self._feeds(list(locator.get("track_ids") or []))
        except ReadDenied:
            raise
        except ReadFailed:
            raise
        except InsufficientPermissionsError as exc:
            raise ReadDenied(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — surface as a failed read
            raise ReadFailed(str(exc)) from exc
        raise ReadFailed(f"unknown object kind {kind}")

    async def _role(self, resource_type: str, resource_id: str) -> None:
        try:
            role = await resolve_role(self.user_id, resource_type, resource_id)
        except InsufficientPermissionsError as exc:
            raise ReadDenied(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ReadFailed(str(exc)) from exc
        if not role:
            raise ReadDenied()

    async def _app(self, object_id: str) -> Optional[Dict[str, Any]]:
        from app.models.nodes import App

        node = await App.get(object_id)
        await self._role("app", object_id)
        if node is None:
            return None
        return {"name": getattr(node, "name", "")}

    async def _track(self, object_id: str) -> Optional[Dict[str, Any]]:
        from app.models.nodes import Track

        node = await Track.get(object_id)
        await self._role("track", object_id)
        if node is None:
            return None
        return {"name": getattr(node, "title", "") or getattr(node, "name", "")}

    async def _entry_types(self, track_id: str) -> List[Any]:
        if track_id not in self._entry_type_cache:
            await self._role("track", track_id)
            from app.services.entry_type_resolver import entry_types_for_track

            self._entry_type_cache[track_id] = list(
                await entry_types_for_track(track_id)
            )
        return self._entry_type_cache[track_id]

    async def _entry_type(self, locator: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        want = _fold(locator.get("name"))
        for entry_type in await self._entry_types(str(locator.get("object_id") or "")):
            if _fold(getattr(entry_type, "name", "")) == want:
                return {"name": entry_type.name}
        return None

    async def _field(self, locator: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        want_type = _fold(locator.get("entry_type"))
        want_key = str(locator.get("field_key") or "")
        for entry_type in await self._entry_types(str(locator.get("object_id") or "")):
            if want_type and _fold(getattr(entry_type, "name", "")) != want_type:
                continue
            for field in (getattr(entry_type, "form_schema", None) or {}).get(
                "fields"
            ) or []:
                if isinstance(field, dict) and str(field.get("key") or "") == want_key:
                    return field
        return None

    async def _tag_names(self, track_id: str) -> List[str]:
        if track_id not in self._tag_cache:
            await self._role("track", track_id)
            from app.models.nodes import Tag

            found = await Tag.find({"context.track_id": track_id})
            if not found:
                found = await Tag.find({"track_id": track_id})
            self._tag_cache[track_id] = [
                getattr(tag, "name", "") for tag in found or []
            ]
        return self._tag_cache[track_id]

    async def _views(self, track_id: str) -> List[Any]:
        if track_id not in self._view_cache:
            await self._role("track", track_id)
            from app.models.nodes import View

            found = await View.find({"context.track_id": track_id})
            if not found:
                found = await View.find({"track_id": track_id})
            self._view_cache[track_id] = list(found or [])
        return self._view_cache[track_id]

    async def _view(self, locator: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from app.models.nodes import View

        track_id = str(locator.get("track_id") or "")
        await self._role("track", track_id)
        view = await View.get(str(locator.get("object_id") or ""))
        if view is None:
            return None
        if track_id and getattr(view, "track_id", "") not in ("", track_id):
            return {
                "name": view.name,
                "type": view.type,
                "is_default": False,
            }
        return {
            "name": view.name,
            "type": view.type,
            "is_default": bool(view.is_default),
        }

    async def _seed(self, object_id: str) -> Optional[Dict[str, Any]]:
        from app.models.nodes import Entry

        node = await Entry.get(object_id)
        await self._role("entry", object_id)
        if node is None:
            return None
        return {"title": node.title, "fields": dict(node.custom_fields or {})}

    async def _dashboard(self, locator: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from app.models.nodes import Dashboard

        await self._role("app", str(locator.get("app_id") or ""))
        node = await Dashboard.get(str(locator.get("object_id") or ""))
        if node is None:
            return None
        titles = []
        for widget in node.widgets or []:
            if isinstance(widget, dict):
                titles.append(str(widget.get("title") or widget.get("name") or ""))
        return {"name": node.name, "titles": titles}

    async def _skill(self, locator: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from app.models.nodes import Skill

        await self._role(
            "app", str(locator.get("app_id") or locator.get("authorized_app_id") or "")
        )
        node = await Skill.get(str(locator.get("object_id") or ""))
        if node is None:
            return None
        return {
            "name": node.name,
            "private": bool(node.private),
            "app_id": node.app_id,
            "key": node.key,
        }

    async def _routine(self, object_id: str) -> Optional[Dict[str, Any]]:
        from app.agentive.nodes import RoutineTask

        node = await RoutineTask.get(object_id)
        if node is None:
            return None
        if getattr(node, "user_id", "") != self.user_id:
            raise ReadDenied()
        return {
            "cron": node.cron,
            "instruction": node.instruction,
            "user_id": node.user_id,
        }

    async def _template_snapshot(
        self, app_id: str, template_key: str
    ) -> Optional[Dict[str, Any]]:
        cache_key = f"{app_id}:{template_key}"
        if cache_key not in self._templates:
            from app.models.nodes import App
            from app.services.app_graph import get_app_attached_operational_model

            app_node = await App.get(app_id)
            await self._role("app", app_id)
            if app_node is None:
                self._templates[cache_key] = {}
            else:
                model = await get_app_attached_operational_model(app_node)
                templates = (
                    (getattr(model, "manifest", None) or {}).get("app") or {}
                ).get("track_templates") or []
                match = next(
                    (
                        item
                        for item in templates
                        if isinstance(item, dict)
                        and str(item.get("key") or "") == template_key
                    ),
                    None,
                )
                self._templates[cache_key] = match or {}
        return self._templates[cache_key] or None

    async def _template_part(self, locator: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        template = await self._template_snapshot(
            str(locator.get("object_id") or ""),
            str(locator.get("template_key") or ""),
        )
        if not template:
            return None
        kind = locator.get("kind")
        if kind == "track_template":
            return {"name": template.get("name") or ""}
        want_type = _fold(locator.get("name") or locator.get("entry_type"))
        for entry_type in template.get("entry_types") or []:
            if not isinstance(entry_type, dict):
                continue
            if want_type and _fold(entry_type.get("name")) != want_type:
                continue
            if kind == "template_entry_type":
                return {"name": entry_type.get("name") or ""}
            for field in entry_type.get("fields") or []:
                if isinstance(field, dict) and str(field.get("key") or "") == str(
                    locator.get("field_key") or ""
                ):
                    return field
        return None

    async def _feeds(self, track_ids: List[str]) -> Dict[str, Any]:
        feed_ids = []
        for track_id in track_ids:
            views = await self._views(track_id)
            if any(getattr(view, "type", "") == "feed" for view in views):
                feed_ids.append(track_id)
        return {"track_ids": track_ids, "feed_track_ids": feed_ids}
