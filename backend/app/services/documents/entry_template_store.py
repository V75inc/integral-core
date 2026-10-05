"""Entry-backed document template store (Document Templates app).

When the ``document_templates`` app is installed in a workspace, template
CRUD and resolution use Entry records on that app's tracks. Legacy
``DocumentTemplate`` / ``DocumentTemplateVersion`` nodes remain readable
during migration (dual-read by id).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type

from app.models.edges import CONTAINS, IS_OF_TYPE
from app.models.nodes import App, Entry, EntryType, Track
from app.services.entry_type_resolver import ensure_entry_type_id, resolve_entry_type_id_by_key
from app.services.workspace_storage_usage import workspace_for_entry
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

DOCUMENT_TEMPLATES_SLUG = "document_templates"
TRACK_TEMPLATES = "templates"


def _installed_package_slug(app: App) -> str:
    """Content-profile slug (monolith) or operational-model package slug (Core)."""
    for attr in (
        "source_profile_slug",
        "source_operational_model_slug",
        "installed_package_slug",
    ):
        val = str(getattr(app, attr, "") or "").strip()
        if val:
            return val
    ctx = getattr(app, "context", None) or {}
    if isinstance(ctx, dict):
        for key in (
            "source_profile_slug",
            "source_operational_model_slug",
            "installed_package_slug",
        ):
            val = str(ctx.get(key) or "").strip()
            if val:
                return val
    return ""
TRACK_VERSIONS = "template_versions"
TRACK_TYPES = "document_types"
TRACK_LAYOUTS = "layouts"
TRACK_GENERATED = "generated_documents"

_EMPTY_DOC: Dict[str, Any] = {
    "type": "doc",
    "content": [{"type": "paragraph", "content": []}],
}


def _slug_track_key(value: str) -> str:
    s = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _legacy_template_node_types() -> Tuple[Optional[Type[Any]], Optional[Type[Any]]]:
    try:
        from app.models.nodes import DocumentTemplate, DocumentTemplateVersion

        return DocumentTemplate, DocumentTemplateVersion
    except ImportError:
        return None, None


@dataclass
class TemplateView:
    """Unified template shape for generation (entry or legacy node)."""

    id: str
    name: str
    module: str
    document_type: str
    context_type: str
    category: str = ""
    status: str = "draft"
    is_default: bool = False
    current_version_id: str = ""
    track_id: str = ""
    app_id: str = ""
    workspace_id: str = ""
    entry_backed: bool = False


@dataclass
class VersionView:
    """Unified version shape for generation."""

    id: str
    template_id: str
    workspace_id: str
    version_number: int = 1
    status: str = "draft"
    editor_document: Dict[str, Any] = field(default_factory=dict)
    token_metadata: Dict[str, Any] = field(default_factory=dict)
    required_inputs: List[Dict[str, Any]] = field(default_factory=list)
    layout_id: str = ""
    header_footer: Dict[str, Any] = field(default_factory=dict)
    checksum: str = ""
    entry_backed: bool = False


def version_content_checksum(ver: VersionView) -> str:
    """Stable hash of template body + tokens (+ stored checksum when present)."""
    stored = str(getattr(ver, "checksum", "") or "").strip()
    if stored:
        return stored
    return _checksum_version_payload(
        ver.editor_document or {},
        ver.token_metadata or {},
        ver.required_inputs or [],
    )


def _cf(entry: Entry) -> Dict[str, Any]:
    return dict(getattr(entry, "custom_fields", None) or {})


def _as_single_id(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0] or "").strip() if value else ""
    return str(value or "").strip()


def _checksum_version_payload(
    editor_document: Dict[str, Any],
    token_metadata: Dict[str, Any],
    required_inputs: List[Dict[str, Any]],
) -> str:
    payload = {
        "editor_document": editor_document or {},
        "token_metadata": token_metadata or {},
        "required_inputs": required_inputs or [],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def find_document_templates_app(workspace_id: str) -> Optional[App]:
    rows: List[Any] = []
    seen: set[str] = set()
    for query in ({"workspace_id": workspace_id}, {"context.workspace_id": workspace_id}):
        found = await App.find(query)
        if found is None:
            continue
        if not isinstance(found, list):
            found = [found]
        for app in found:
            aid = str(getattr(app, "id", "") or "")
            if aid and aid not in seen:
                seen.add(aid)
                rows.append(app)
    if not rows:
        return None
    slug_fold = DOCUMENT_TEMPLATES_SLUG.casefold()
    for app in rows:
        if str(getattr(app, "lifecycle_state", "") or "active") == "uninstalled":
            continue
        if _installed_package_slug(app).casefold() == slug_fold:
            return app
    return None


async def require_document_templates_app(workspace_id: str) -> App:
    from app.exceptions import AppDependencyError

    app = await find_document_templates_app(workspace_id)
    if not app:
        raise AppDependencyError(
            message=(
                "Document Templates app is not installed in this workspace. "
                "Install it from Manage Apps before using document templates."
            ),
            details={"missing_deps": [DOCUMENT_TEMPLATES_SLUG]},
        )
    return app


async def is_entry_mode(workspace_id: str) -> bool:
    return await find_document_templates_app(workspace_id) is not None


async def _tracks_by_key(app: App) -> Dict[str, Track]:
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    out: Dict[str, Track] = {}
    for t in tracks or []:
        key = str(getattr(t, "template_id", "") or "").strip()
        if key:
            out[key] = t
        title_key = _slug_track_key(str(getattr(t, "title", "") or ""))
        if title_key and title_key not in out:
            out[title_key] = t
    return out


async def _get_track(app: App, track_key: str) -> Optional[Track]:
    return (await _tracks_by_key(app)).get(track_key)


async def _find_document_type_entry_id(app: App, code: str) -> str:
    """Resolve a document type slug to an entry id on the document_types track."""
    normalized = (code or "").strip()
    if not normalized:
        return ""
    track = await _get_track(app, TRACK_TYPES)
    if not track:
        return ""
    rows = await Entry.find({"context.track_id": str(track.id)}) or []
    if not isinstance(rows, list):
        rows = [rows]
    for row in rows:
        if str(_cf(row).get("code") or "").strip() == normalized:
            return str(row.id)
    return ""


async def _resolve_document_type_code(entry: Entry) -> str:
    """Read denormalized code or resolve from relation / catalog node."""
    cf = _cf(entry)
    code = str(cf.get("document_type_code") or "").strip()
    if code:
        return code
    rel_id = _as_single_id(cf.get("document_type"))
    if not rel_id:
        return ""
    rel = await Entry.get(rel_id)
    if rel:
        rel_code = str(_cf(rel).get("code") or "").strip()
        if rel_code:
            return rel_code
    try:
        from app.models.nodes import DocumentType

        dt = await DocumentType.get(rel_id)
        if dt:
            return str(getattr(dt, "code", "") or "").strip()
    except ImportError:
        pass
    return ""


def _template_view_from_entry(entry: Entry, *, app_id: str = "") -> TemplateView:
    cf = _cf(entry)
    return TemplateView(
        id=str(entry.id),
        name=str(getattr(entry, "title", "") or ""),
        module=str(cf.get("consumer_module") or ""),
        document_type=str(cf.get("document_type_code") or ""),
        context_type=str(cf.get("context_type") or ""),
        status=str(cf.get("status") or "draft"),
        is_default=bool(cf.get("is_default")),
        current_version_id=_as_single_id(cf.get("current_version")),
        track_id=str(cf.get("consumer_track_id") or ""),
        app_id=app_id,
        workspace_id=str(getattr(entry, "workspace_id", "") or ""),
        entry_backed=True,
    )


def _version_view_from_entry(entry: Entry, *, template_id: str = "") -> VersionView:
    cf = _cf(entry)
    tid = template_id or _as_single_id(cf.get("template"))
    return VersionView(
        id=str(entry.id),
        template_id=tid,
        workspace_id=str(getattr(entry, "workspace_id", "") or ""),
        version_number=int(cf.get("version_number") or 1),
        status=str(cf.get("status") or "draft"),
        editor_document=dict(cf.get("editor_document") or _EMPTY_DOC),
        token_metadata=dict(cf.get("token_metadata") or {"tokens": []}),
        required_inputs=list(cf.get("required_inputs") or []),
        layout_id=str(cf.get("layout_id") or ""),
        header_footer=dict(cf.get("header_footer") or {}),
        checksum=str(cf.get("checksum") or ""),
        entry_backed=True,
    )


def _template_view_from_node(node: Any) -> TemplateView:
    return TemplateView(
        id=str(node.id),
        name=str(getattr(node, "name", "") or ""),
        module=str(getattr(node, "module", "") or ""),
        document_type=str(getattr(node, "document_type", "") or ""),
        context_type=str(getattr(node, "context_type", "") or ""),
        category=str(getattr(node, "category", "") or ""),
        status=str(getattr(node, "status", "") or "draft"),
        is_default=bool(getattr(node, "is_default", False)),
        current_version_id=str(getattr(node, "current_version_id", "") or ""),
        track_id=str(getattr(node, "track_id", "") or ""),
        app_id=str(getattr(node, "app_id", "") or ""),
        workspace_id=str(getattr(node, "workspace_id", "") or ""),
        entry_backed=False,
    )


def _version_view_from_node(node: Any) -> VersionView:
    return VersionView(
        id=str(node.id),
        template_id=str(getattr(node, "template_id", "") or ""),
        workspace_id=str(getattr(node, "workspace_id", "") or ""),
        version_number=int(getattr(node, "version_number", 1) or 1),
        status=str(getattr(node, "status", "") or "draft"),
        editor_document=dict(getattr(node, "editor_document", None) or _EMPTY_DOC),
        token_metadata=dict(getattr(node, "token_metadata", None) or {"tokens": []}),
        required_inputs=list(getattr(node, "required_inputs", None) or []),
        layout_id=str(getattr(node, "layout_id", "") or ""),
        header_footer=dict(getattr(node, "header_footer", None) or {}),
        checksum=str(getattr(node, "checksum", "") or ""),
        entry_backed=False,
    )


async def _entry_belongs_to_workspace(entry: Entry, workspace_id: str) -> bool:
    ws = await workspace_for_entry(entry)
    return ws is not None and str(ws.id) == workspace_id


async def _load_template_entry(template_id: str, workspace_id: str) -> Optional[Entry]:
    entry = await Entry.get(template_id)
    if not entry or not await _entry_belongs_to_workspace(entry, workspace_id):
        return None
    await ensure_entry_type_id(entry, type_key="template")
    return entry


async def _load_version_entry(version_id: str, workspace_id: str) -> Optional[Entry]:
    entry = await Entry.get(version_id)
    if not entry or not await _entry_belongs_to_workspace(entry, workspace_id):
        return None
    await ensure_entry_type_id(entry, type_key="template_version")
    return entry


async def get_template_view(
    *, workspace_id: str, template_id: str
) -> Optional[TemplateView]:
    entry = await _load_template_entry(template_id, workspace_id)
    if entry:
        app = await find_document_templates_app(workspace_id)
        view = _template_view_from_entry(entry, app_id=str(app.id) if app else "")
        if not view.document_type:
            view.document_type = await _resolve_document_type_code(entry)
        return view
    DocumentTemplate, _ = _legacy_template_node_types()
    if DocumentTemplate is not None:
        node = await DocumentTemplate.get(template_id)
        if node and str(getattr(node, "workspace_id", "") or "") == workspace_id:
            return _template_view_from_node(node)
    return None


async def get_version_view(
    *, workspace_id: str, version_id: str
) -> Optional[VersionView]:
    entry = await _load_version_entry(version_id, workspace_id)
    if entry:
        return _version_view_from_entry(entry)
    _, DocumentTemplateVersion = _legacy_template_node_types()
    if DocumentTemplateVersion is not None:
        node = await DocumentTemplateVersion.get(version_id)
        if node and str(getattr(node, "workspace_id", "") or "") == workspace_id:
            return _version_view_from_node(node)
    return None


async def list_template_views(
    *,
    workspace_id: str,
    module: Optional[str] = None,
    context_type: Optional[str] = None,
    document_type: Optional[str] = None,
    status: Optional[str] = None,
) -> List[TemplateView]:
    app = await find_document_templates_app(workspace_id)
    if app:
        track = await _get_track(app, TRACK_TEMPLATES)
        if not track:
            return []
        query: Dict[str, Any] = {"context.track_id": str(track.id)}
        rows = await Entry.find(query) or []
        if not isinstance(rows, list):
            rows = [rows]
        out: List[TemplateView] = []
        for row in rows:
            view = _template_view_from_entry(row, app_id=str(app.id))
            if not view.document_type:
                view.document_type = await _resolve_document_type_code(row)
            if module and view.module != module:
                continue
            if context_type and view.context_type != context_type:
                continue
            if document_type and view.document_type != document_type:
                continue
            if status and view.status != status:
                continue
            out.append(view)
        return out

    query = {"context.workspace_id": workspace_id}
    if module:
        query["context.module"] = module
    if context_type:
        query["context.context_type"] = context_type
    if document_type:
        query["context.document_type"] = document_type
    if status:
        query["context.status"] = status
    DocumentTemplate, _ = _legacy_template_node_types()
    if DocumentTemplate is None:
        return []
    rows = await DocumentTemplate.find(query) or []
    if not isinstance(rows, list):
        rows = [rows]
    return [_template_view_from_node(r) for r in rows]


async def resolve_for_generate(
    *,
    workspace_id: str,
    template_id: Optional[str] = None,
    template_version_id: Optional[str] = None,
    module: Optional[str] = None,
    document_type: Optional[str] = None,
) -> Tuple[TemplateView, VersionView]:
    tmpl: Optional[TemplateView] = None
    ver: Optional[VersionView] = None

    if template_version_id:
        ver = await get_version_view(workspace_id=workspace_id, version_id=template_version_id)
        if not ver:
            raise ValueError("Template version not found")
        tmpl = await get_template_view(workspace_id=workspace_id, template_id=ver.template_id)
    elif template_id:
        tmpl = await get_template_view(workspace_id=workspace_id, template_id=template_id)
    elif module and document_type:
        rows = await list_template_views(
            workspace_id=workspace_id,
            module=module,
            document_type=document_type,
            status="active",
        )
        tmpl = next((r for r in rows if r.is_default), None) or (rows[0] if rows else None)

    if not tmpl:
        raise ValueError("Template not found")
    if tmpl.status == "archived":
        raise ValueError("Template is archived")

    if ver is None:
        if not tmpl.current_version_id:
            raise ValueError("Template has no published version")
        ver = await get_version_view(
            workspace_id=workspace_id, version_id=tmpl.current_version_id
        )
    if not ver:
        raise ValueError("Template version not found")
    if ver.status == "draft" and not template_version_id:
        raise ValueError("Template has no published version")
    return tmpl, ver


async def create_template_entry(
    *,
    user_id: str,
    workspace_id: str,
    name: str,
    module: str,
    document_type: str,
    context_type: str,
    is_default: bool = False,
    consumer_track_id: str = "",
    editor_document: Optional[Dict[str, Any]] = None,
    token_metadata: Optional[Dict[str, Any]] = None,
    required_inputs: Optional[List[Dict[str, Any]]] = None,
) -> TemplateView:
    app = await require_document_templates_app(workspace_id)
    templates_track = await _get_track(app, TRACK_TEMPLATES)
    versions_track = await _get_track(app, TRACK_VERSIONS)
    if not templates_track or not versions_track:
        raise ValueError("Document Templates tracks not provisioned")

    now = utc_now_iso()
    name = (name or "").strip() or "Untitled template"
    doc = editor_document if isinstance(editor_document, dict) else dict(_EMPTY_DOC)
    meta = token_metadata if isinstance(token_metadata, dict) else {"tokens": []}
    inputs = list(required_inputs or [])

    doc_type_code = (document_type or "").strip()
    tmpl_cf: Dict[str, Any] = {
        "consumer_module": (module or "").strip(),
        "document_type_code": doc_type_code,
        "context_type": (context_type or "").strip(),
        "status": "draft",
        "is_default": bool(is_default),
        "current_version": "",
        "consumer_track_id": (consumer_track_id or "").strip(),
    }
    type_entry_id = await _find_document_type_entry_id(app, doc_type_code)
    if type_entry_id:
        tmpl_cf["document_type"] = type_entry_id
    template_type_id = await resolve_entry_type_id_by_key(
        str(templates_track.id), "template"
    )
    tmpl_entry = await Entry.create(
        title=name,
        body="",
        track_id=str(templates_track.id),
        type_id=template_type_id or None,
        custom_fields=tmpl_cf,
        created_at=now,
        updated_at=now,
    )
    await templates_track.connect(tmpl_entry, edge=CONTAINS, added_at=now)
    if template_type_id:
        template_et = await EntryType.get(template_type_id)
        if template_et:
            await tmpl_entry.connect(template_et, edge=IS_OF_TYPE, assigned_at=now)

    version_type_id = await resolve_entry_type_id_by_key(
        str(versions_track.id), "template_version"
    )
    ver_entry = await Entry.create(
        title="v1",
        body="",
        track_id=str(versions_track.id),
        type_id=version_type_id or None,
        custom_fields={
            "template": str(tmpl_entry.id),
            "version_number": 1,
            "status": "draft",
            "editor_document": doc,
            "token_metadata": meta,
            "required_inputs": inputs,
            "checksum": _checksum_version_payload(doc, meta, inputs),
            "layout_id": "",
            "header_footer": {},
        },
        created_at=now,
        updated_at=now,
    )
    await versions_track.connect(ver_entry, edge=CONTAINS, added_at=now)
    if version_type_id:
        version_et = await EntryType.get(version_type_id)
        if version_et:
            await ver_entry.connect(version_et, edge=IS_OF_TYPE, assigned_at=now)

    cf = _cf(tmpl_entry)
    cf["current_version"] = str(ver_entry.id)
    tmpl_entry.custom_fields = cf
    tmpl_entry.updated_at = now
    await tmpl_entry.save()

    if is_default:
        await _clear_other_defaults_entry(
            workspace_id=workspace_id,
            module=module,
            document_type=document_type,
            keep_id=str(tmpl_entry.id),
            app=app,
        )

    return _template_view_from_entry(tmpl_entry, app_id=str(app.id))


async def _clear_other_defaults_entry(
    *,
    workspace_id: str,
    module: str,
    document_type: str,
    keep_id: str,
    app: App,
) -> None:
    for view in await list_template_views(
        workspace_id=workspace_id,
        module=module,
        document_type=document_type,
        status="active",
    ):
        if view.id == keep_id or not view.is_default:
            continue
        entry = await Entry.get(view.id)
        if not entry:
            continue
        cf = _cf(entry)
        cf["is_default"] = False
        entry.custom_fields = cf
        entry.updated_at = utc_now_iso()
        await entry.save()


async def update_template_entry(
    *,
    workspace_id: str,
    template_id: str,
    name: Optional[str] = None,
    module: Optional[str] = None,
    document_type: Optional[str] = None,
    context_type: Optional[str] = None,
    track_id: Optional[str] = None,
    status: Optional[str] = None,
    is_default: Optional[bool] = None,
) -> TemplateView:
    app = await require_document_templates_app(workspace_id)
    entry = await _load_template_entry(template_id, workspace_id)
    if not entry:
        raise ValueError("Template not found")
    cf = _cf(entry)
    if name is not None:
        entry.title = name.strip() or entry.title
    if module is not None:
        cf["consumer_module"] = module.strip()
    if document_type is not None:
        doc_type_code = document_type.strip()
        cf["document_type_code"] = doc_type_code
        type_entry_id = await _find_document_type_entry_id(app, doc_type_code)
        if type_entry_id:
            cf["document_type"] = type_entry_id
        elif "document_type" in cf and not doc_type_code:
            cf.pop("document_type", None)
    if context_type is not None:
        cf["context_type"] = context_type.strip()
    if track_id is not None:
        cf["consumer_track_id"] = track_id.strip()
    if status is not None:
        if status not in ("draft", "active", "inactive", "archived"):
            raise ValueError(f"Invalid status: {status}")
        cf["status"] = status
    if is_default is not None:
        cf["is_default"] = bool(is_default)
    entry.custom_fields = cf
    entry.updated_at = utc_now_iso()
    await entry.save()
    if cf.get("is_default"):
        await _clear_other_defaults_entry(
            workspace_id=workspace_id,
            module=str(cf.get("consumer_module") or ""),
            document_type=str(cf.get("document_type_code") or ""),
            keep_id=str(entry.id),
            app=app,
        )
    return _template_view_from_entry(entry, app_id=str(app.id))


async def publish_version_entry(
    *,
    workspace_id: str,
    version_id: str,
    user_id: str,
) -> VersionView:
    await require_document_templates_app(workspace_id)
    ver_entry = await _load_version_entry(version_id, workspace_id)
    if not ver_entry:
        raise ValueError("Version not found")
    cf = _cf(ver_entry)
    if cf.get("status") == "published":
        return _version_view_from_entry(ver_entry)

    template_id = _as_single_id(cf.get("template"))
    tmpl_entry = await _load_template_entry(template_id, workspace_id)
    if not tmpl_entry:
        raise ValueError("Template not found")

    now = utc_now_iso()
    app = await find_document_templates_app(workspace_id)
    versions_track = await _get_track(app, TRACK_VERSIONS) if app else None
    if versions_track:
        siblings = await Entry.find({"context.track_id": str(versions_track.id)}) or []
        if not isinstance(siblings, list):
            siblings = [siblings]
        for sib in siblings:
            scf = _cf(sib)
            if _as_single_id(scf.get("template")) != template_id:
                continue
            if scf.get("status") == "published" and str(sib.id) != version_id:
                scf["status"] = "superseded"
                sib.custom_fields = scf
                sib.updated_at = now
                await sib.save()

    from app.services.documents.signature_embed import snapshot_pre_embedded_signatures

    doc = dict(cf.get("editor_document") or _EMPTY_DOC)
    meta = dict(cf.get("token_metadata") or {})
    snap_doc, snap_meta = await snapshot_pre_embedded_signatures(doc, meta)
    cf["editor_document"] = snap_doc
    cf["token_metadata"] = snap_meta
    cf["checksum"] = _checksum_version_payload(
        snap_doc,
        snap_meta,
        list(cf.get("required_inputs") or []),
    )
    cf["status"] = "published"
    cf["published_at"] = now
    ver_entry.custom_fields = cf
    ver_entry.updated_at = now
    await ver_entry.save()

    tcf = _cf(tmpl_entry)
    tcf["current_version"] = str(ver_entry.id)
    tcf["status"] = "active"
    tmpl_entry.custom_fields = tcf
    tmpl_entry.updated_at = now
    await tmpl_entry.save()

    return _version_view_from_entry(ver_entry, template_id=template_id)


def template_view_to_api(view: TemplateView, entry: Optional[Entry] = None) -> Dict[str, Any]:
    """Serialize TemplateView for REST responses."""
    created = getattr(entry, "created_at", None) if entry else None
    updated = getattr(entry, "updated_at", None) if entry else None
    return {
        "id": view.id,
        "entity": "Entry",
        "name": view.name,
        "module": view.module,
        "document_type": view.document_type,
        "context_type": view.context_type,
        "category": view.category,
        "status": view.status,
        "is_default": view.is_default,
        "current_version_id": view.current_version_id,
        "app_id": view.app_id,
        "track_id": view.track_id,
        "workspace_id": view.workspace_id,
        "entry_backed": view.entry_backed,
        "created_at": created,
        "updated_at": updated,
    }


def version_view_to_api(view: VersionView, entry: Optional[Entry] = None) -> Dict[str, Any]:
    created = getattr(entry, "created_at", None) if entry else None
    updated = getattr(entry, "updated_at", None) if entry else None
    return {
        "id": view.id,
        "entity": "Entry",
        "template_id": view.template_id,
        "version_number": view.version_number,
        "status": view.status,
        "editor_document": view.editor_document,
        "token_metadata": view.token_metadata,
        "required_inputs": view.required_inputs,
        "layout_id": view.layout_id,
        "header_footer": view.header_footer,
        "workspace_id": view.workspace_id,
        "entry_backed": view.entry_backed,
        "created_at": created,
        "updated_at": updated,
    }


async def list_version_views(
    *, workspace_id: str, template_id: str
) -> List[VersionView]:
    app = await find_document_templates_app(workspace_id)
    if not app:
        return []
    track = await _get_track(app, TRACK_VERSIONS)
    if not track:
        return []
    rows = await Entry.find({"context.track_id": str(track.id)}) or []
    if not isinstance(rows, list):
        rows = [rows]
    out: List[VersionView] = []
    for row in rows:
        cf = _cf(row)
        if _as_single_id(cf.get("template")) != template_id:
            continue
        out.append(_version_view_from_entry(row, template_id=template_id))
    out.sort(key=lambda v: v.version_number, reverse=True)
    return out


async def update_version_draft_entry(
    *,
    workspace_id: str,
    version_id: str,
    editor_document: Optional[Dict[str, Any]] = None,
    token_metadata: Optional[Dict[str, Any]] = None,
    required_inputs: Optional[List[Dict[str, Any]]] = None,
    layout_id: Optional[str] = None,
    header_footer: Optional[Dict[str, Any]] = None,
    render_html: Optional[str] = None,
) -> VersionView:
    await require_document_templates_app(workspace_id)
    ver_entry = await _load_version_entry(version_id, workspace_id)
    if not ver_entry:
        raise ValueError("Version not found")
    cf = _cf(ver_entry)
    if cf.get("status") != "draft":
        raise ValueError("Only draft versions can be edited")
    if editor_document is not None:
        cf["editor_document"] = editor_document
    if token_metadata is not None:
        cf["token_metadata"] = token_metadata
    if required_inputs is not None:
        cf["required_inputs"] = required_inputs
    if layout_id is not None:
        cf["layout_id"] = layout_id
    if header_footer is not None:
        cf["header_footer"] = header_footer
    if render_html is not None:
        cf["render_html"] = render_html
    cf["checksum"] = _checksum_version_payload(
        cf.get("editor_document") or {},
        cf.get("token_metadata") or {},
        list(cf.get("required_inputs") or []),
    )
    ver_entry.custom_fields = cf
    ver_entry.updated_at = utc_now_iso()
    await ver_entry.save()
    return _version_view_from_entry(ver_entry)


def extract_relation_entry_id(value: Any) -> str:
    """Resolve an entry id from a relation field value."""
    if isinstance(value, dict):
        return str(value.get("id") or value.get("entry_id") or "").strip()
    return _as_single_id(value)


def extract_contract_template_id(cf: Dict[str, Any]) -> str:
    """Read contract template id from relation or legacy scalar field."""
    rel = cf.get("contract_template")
    if rel not in (None, "", []):
        tid = extract_relation_entry_id(rel)
        if tid:
            return tid
    return _as_single_id(cf.get("contract_template_id"))


async def delete_template_entry(*, workspace_id: str, template_id: str) -> str:
    """Remove template entry and its version entries."""
    await require_document_templates_app(workspace_id)
    app = await find_document_templates_app(workspace_id)
    assert app
    entry = await _load_template_entry(template_id, workspace_id)
    if not entry:
        raise ValueError("Template not found")
    for ver in await list_version_views(
        workspace_id=workspace_id, template_id=template_id
    ):
        ver_entry = await Entry.get(ver.id)
        if ver_entry:
            await ver_entry.delete(cascade=False)
    await entry.delete(cascade=False)
    return template_id


async def resolve_layout_parts(
    layout_id: str, header_footer: Dict[str, Any]
) -> Dict[str, Any]:
    """Resolve letterhead from layout entry id or legacy DocumentLayout node."""
    from app.services.documents.document_theme import (
        DEFAULT_PAGE_SIZE,
        normalize_margins,
        normalize_page_size,
    )

    hf = dict(header_footer or {})
    header = str(hf.get("header_html") or "")
    footer = str(hf.get("footer_html") or "")
    page_size = DEFAULT_PAGE_SIZE
    margins = normalize_margins(None)
    page_numbers = True
    if layout_id:
        layout_entry = await Entry.get(layout_id)
        if layout_entry:
            lcf = _cf(layout_entry)
            if not header:
                header = str(lcf.get("header_html") or "")
            if not footer:
                footer = str(lcf.get("footer_html") or "")
            page_size = normalize_page_size(lcf.get("page_size"))
            margins = normalize_margins(lcf.get("margins"))
            page_numbers = bool(lcf.get("page_numbers", True))
        else:
            try:
                from app.models.nodes import DocumentLayout

                layout = await DocumentLayout.get(layout_id)
            except ImportError:
                layout = None
            if layout:
                if not header:
                    header = layout.header_html or ""
                if not footer:
                    footer = layout.footer_html or ""
                page_size = normalize_page_size(getattr(layout, "page_size", None))
                margins = normalize_margins(getattr(layout, "margins", None))
                page_numbers = bool(getattr(layout, "page_numbers", True))
    if hf.get("page_size"):
        page_size = normalize_page_size(str(hf.get("page_size")))
    if isinstance(hf.get("margins"), dict) and hf.get("margins"):
        margins = normalize_margins(hf.get("margins"))
    if "page_numbers" in hf:
        page_numbers = bool(hf.get("page_numbers"))
    return {
        "header_html": header,
        "footer_html": footer,
        "page_size": page_size,
        "margins": margins,
        "page_numbers": page_numbers,
    }


async def create_generated_document_entry(
    *,
    workspace_id: str,
    template_id: str,
    template_version_id: str,
    module: str,
    context_type: str,
    context_entry_id: str,
    generated_by: str,
    output_format: str,
    attachment_id: str,
    input_values: Dict[str, Any],
    resolved_snapshot: Dict[str, Any],
    checksum: str,
    title: str,
) -> Entry:
    """Log a generated document on the Document Templates app track."""
    app = await find_document_templates_app(workspace_id)
    if not app:
        raise ValueError("Document Templates app not installed")
    track = await _get_track(app, TRACK_GENERATED)
    if not track:
        raise ValueError("Generated documents track missing")
    now = utc_now_iso()
    gd_entry = await Entry.create(
        title=title or "Generated document",
        body="",
        track_id=str(track.id),
        workspace_id=workspace_id,
        custom_fields={
            "template": template_id,
            "template_version": template_version_id,
            "consumer_module": module,
            "context_type": context_type,
            "context_entry": context_entry_id,
            "output_format": output_format,
            "attachment_id": attachment_id,
            "resolved_snapshot": resolved_snapshot,
            "checksum": checksum,
            "generated_by": generated_by,
            "generated_at": now,
            "status": "generated",
        },
        created_at=now,
        updated_at=now,
    )
    await track.connect(gd_entry, edge=CONTAINS, added_at=now)
    return gd_entry


async def create_version_draft_entry(
    *, workspace_id: str, template_id: str, user_id: str
) -> VersionView:
    await require_document_templates_app(workspace_id)
    app = await find_document_templates_app(workspace_id)
    assert app
    versions_track = await _get_track(app, TRACK_VERSIONS)
    if not versions_track:
        raise ValueError("Template versions track missing")
    existing = await list_version_views(workspace_id=workspace_id, template_id=template_id)
    source_entry = None
    tmpl_entry = await _load_template_entry(template_id, workspace_id)
    if not tmpl_entry:
        raise ValueError("Template not found")
    tcf = _cf(tmpl_entry)
    cur_id = _as_single_id(tcf.get("current_version"))
    if cur_id:
        source_entry = await _load_version_entry(cur_id, workspace_id)
    if source_entry is None and existing:
        source_entry = await _load_version_entry(existing[0].id, workspace_id)
    scf = _cf(source_entry) if source_entry else {}
    next_num = max((v.version_number for v in existing), default=0) + 1
    now = utc_now_iso()
    doc = dict(scf.get("editor_document") or _EMPTY_DOC)
    meta = dict(scf.get("token_metadata") or {"tokens": []})
    inputs = list(scf.get("required_inputs") or [])
    version_type_id = await resolve_entry_type_id_by_key(
        str(versions_track.id), "template_version"
    )
    ver_entry = await Entry.create(
        title=f"v{next_num}",
        body="",
        track_id=str(versions_track.id),
        type_id=version_type_id or None,
        workspace_id=workspace_id,
        custom_fields={
            "template": template_id,
            "version_number": next_num,
            "status": "draft",
            "editor_document": doc,
            "token_metadata": meta,
            "required_inputs": inputs,
            "layout_id": str(scf.get("layout_id") or ""),
            "header_footer": dict(scf.get("header_footer") or {}),
            "checksum": _checksum_version_payload(doc, meta, inputs),
        },
        created_at=now,
        updated_at=now,
    )
    await versions_track.connect(ver_entry, edge=CONTAINS, added_at=now)
    if version_type_id:
        version_et = await EntryType.get(version_type_id)
        if version_et:
            await ver_entry.connect(version_et, edge=IS_OF_TYPE, assigned_at=now)
    return _version_view_from_entry(ver_entry, template_id=template_id)
