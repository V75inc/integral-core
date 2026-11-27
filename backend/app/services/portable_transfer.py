"""Permission-scoped, bounded, data-only resource export and restore.

Archives never carry grants, identities, credentials, installed-artifact authority,
executable bundles, or derived search indexes. Restore always creates private copies.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from copy import deepcopy
from functools import wraps
from typing import Any

from jvspatial.core import Node, Walker
from jvspatial.core.decorators import on_visit
from pydantic import Field, ValidationError

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.api.validators_common import compute_fold
from app.models.edges import CONTAINS, HAS_ATTACHMENT, HAS_VIEW
from app.models.nodes import (
    App,
    Attachment,
    Entry,
    EntryType,
    Tag,
    Track,
)
from app.schemas.policy import Resource, Subject
from app.schemas.portable_transfer import (
    ExportSelection,
    PackagePayload,
    PortableApp,
    PortableArchive,
    PortableDashboard,
    PortableEntry,
    PortableFile,
    PortableTag,
    PortableTrack,
    PortableType,
    PortableView,
)
from app.services.app_graph import (
    create_track_view,
    get_app_attached_operational_model,
    get_track_attached_operational_model,
)
from app.services.attachment_storage import get_attachment_storage_service
from app.services.policy_engine import evaluate
from app.services.portable_archive import decode_archive, encode_archive
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

MAX_NODES = 10000
# These contracts depend on separately admitted installed code/operations. A ZIP
# cannot promote that code or bypass its guards by flattening it to a blank App.
# Version-2 archives may retain them when a verified package payload is bound.
_RUNTIME_KEYS = {
    "tools",
    "hooks",
    "operations",
    "protected_state",
    "plugins",
    "extension_views",
    "requires_apps",
    "agents",
}


async def _authorize(
    user_id: str, kind: str, identity: str, action: str = "read"
) -> None:
    decision = await evaluate(
        subject=Subject(kind="human", id=user_id),
        action=f"{kind}.{action}",
        resource=Resource(kind=kind, id=identity, scope=f"{kind}:{identity}"),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="You cannot transfer all of the selected content"
        )


def _manifest_tiers(manifest: dict) -> list[dict]:
    app_tier = manifest.get("app") or {}
    tiers = [manifest, app_tier, manifest.get("track") or {}]
    if isinstance(app_tier, dict):
        tiers += list(app_tier.get("tracks") or []) + list(
            app_tier.get("track_templates") or []
        )
    return [tier for tier in tiers if isinstance(tier, dict)]


def _tier_has_runtime(value: dict) -> bool:
    skills = value.get("skills") or []
    if isinstance(skills, list) and any(
        not isinstance(skill, dict) or skill.get("kind", "declarative") != "declarative"
        for skill in skills
    ):
        return True
    if any(value.get(key) for key in _RUNTIME_KEYS):
        return True
    queries = value.get("queries") or []
    return isinstance(queries, list) and any(
        isinstance(query, dict) and (query.get("handler_ref") or query.get("tool"))
        for query in queries
    )


def manifest_has_runtime(manifest: dict | None) -> bool:
    """True when the declaration depends on installed package runtime."""
    if not isinstance(manifest, dict):
        return False
    try:
        return any(_tier_has_runtime(tier) for tier in _manifest_tiers(manifest))
    except Exception:
        return False


def _data_manifest(manifest: dict, *, allow_runtime: bool = False) -> dict:
    """No settings values; executable authority only when a package is bound."""
    result = deepcopy(manifest)
    if result.get("scope") == "app_node":
        result["scope"] = "app"
    # Runtime declarations can also live on embedded Track templates. Walk the
    # declaration, not just its top tier, before removing nonportable values.
    for value in _manifest_tiers(result):
        skills = value.get("skills") or []
        if not isinstance(skills, list):
            raise BadRequestError(message="Invalid portable configuration tier")
        if not allow_runtime and any(
            not isinstance(skill, dict)
            or skill.get("kind", "declarative") != "declarative"
            for skill in skills
        ):
            raise BadRequestError(
                message="Custom executable skills require their original installed package"
            )
        if not allow_runtime and any(value.get(key) for key in _RUNTIME_KEYS):
            raise BadRequestError(
                message="Portable configuration requires separately installed runtime extensions"
            )
        queries = value.get("queries") or []
        if not isinstance(queries, list) or (
            not allow_runtime
            and any(
                not isinstance(query, dict)
                or query.get("handler_ref")
                or query.get("tool")
                for query in queries
            )
        ):
            raise BadRequestError(
                message="Custom query handlers require their original installed runtime package"
            )
    if not allow_runtime:
        result.pop("package", None)
    result.pop("migrations", None)
    result.pop("applied_migrations", None)
    result.pop("materialization", None)
    for tier in (result, result.get("app", {}), result.get("track", {})):
        if not isinstance(tier, dict):
            raise BadRequestError(message="Invalid configuration in archive")
        if not allow_runtime and any(tier.get(key) for key in _RUNTIME_KEYS):
            raise BadRequestError(
                message=(
                    "This configuration requires installed extensions or guarded operations. "
                    "Use the original signed package and its data migration tools; "
                    "a portable archive cannot replace its runtime contracts."
                )
            )
        drop = (
            {"seeds", "settings"}
            if allow_runtime
            else _RUNTIME_KEYS | {"seeds", "settings"}
        )
        for key in drop:
            tier.pop(key, None)
    pending = [result]
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            if value.get("writeOnly") or value.get("format") == "password":
                for key in ("default", "example", "examples"):
                    value.pop(key, None)
            pending.extend(value.values())
    return result


def _export_settings(value: Any, schema: dict) -> Any:
    """Export only declared, non-secret settings, including nested objects."""
    if schema.get("writeOnly") or schema.get("format") == "password":
        return None
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        return {
            key: _export_settings(item, properties[key])
            for key, item in value.items()
            if key in properties
            and not properties[key].get("writeOnly")
            and properties[key].get("format") != "password"
        }
    if isinstance(value, list):
        return [_export_settings(item, schema.get("items") or {}) for item in value]
    return deepcopy(value)


_TEXT_KEYS = {
    "title",
    "name",
    "label",
    "description",
    "purpose",
    "body",
    "text",
    "instructions",
    "prompt",
    "placeholder",
    "help",
    "tooltip",
}


def _remap(
    value: Any,
    mapping: dict[str, str],
    *,
    identities: bool = False,
    app_names: dict[str, str] | None = None,
) -> Any:
    """Rebind structural references without treating ordinary literals as IDs."""
    if isinstance(value, str):
        if value.startswith("instance:") and value[9:] in mapping:
            return "instance:" + mapping[value[9:]]
        if identities or value.startswith(("n.", "seed:", "ref:")):
            return mapping.get(value, value)
        return value
    if isinstance(value, list):
        return [
            _remap(item, mapping, identities=identities, app_names=app_names)
            for item in value
        ]
    if isinstance(value, dict):
        result = {
            key: (
                item
                if key in _TEXT_KEYS
                else _remap(
                    item,
                    mapping,
                    identities=key == "id"
                    or key.endswith(("_id", "_ids", "_ref", "_refs")),
                    app_names=app_names,
                )
            )
            for key, item in value.items()
        }
        resolution = str(value.get("resolution") or "")
        if (
            app_names
            and value.get("allow_cross_app")
            and resolution.startswith("instance:")
        ):
            target = resolution[9:]
            if target in app_names:
                result["target_app"] = app_names[target]
        return result
    return value


def _body_links(text: str, mapping: dict[str, str]) -> str:
    """Rebind resource links while preserving plain text that resembles an ID."""
    return re.sub(
        r"(?P<prefix>/(?:api/)?(?:attachments|entries|tracks|apps)/)(?P<ref>n\.[A-Za-z]+\.[A-Za-z0-9_-]+)",
        lambda match: match["prefix"] + mapping.get(match["ref"], match["ref"]),
        text,
    )


def _field_base(field: dict, manifest: dict | None = None) -> str:
    """Resolve archive-scoped composite primitives for reference closure/remapping."""
    base = (field.get("composite") or {}).get("base")
    if base:
        return str(base)
    key = field.get("type")
    for composite in (manifest or {}).get("field_types", []):
        if composite.get("key") == key:
            return str(composite.get("base") or key or "text")
    return str(key or "text")


def _field_values(
    entry: PortableEntry,
    entry_type: PortableType,
    mapping: dict[str, str],
    manifest: dict | None = None,
) -> dict:
    values = deepcopy(entry.custom_fields)
    fields = entry_type.form_schema.get("fields", [])
    for field in fields:
        key = field.get("key")
        base = _field_base(field, manifest)
        if (
            base
            in {
                "relation",
                "multi_relation",
                "relations",
                "file",
                "files",
                "details_track",
            }
            and key in values
        ):
            values[key] = _remap(values[key], mapping, identities=True)
    if "_card_preview_attachment_id" in values:
        values["_card_preview_attachment_id"] = _remap(
            values["_card_preview_attachment_id"], mapping, identities=True
        )
    for field in fields:
        if _field_base(field, manifest) in {"richtext", "markdown"} and isinstance(
            values.get(field.get("key")), str
        ):
            key = field["key"]
            values[key] = _body_links(values[key], mapping)
    return values


def _fail_closed(function):
    """Propagate hook failures instead of returning a truncated archive."""

    @wraps(function)
    async def guarded(self, node):
        if self.failure is not None:
            return
        try:
            await function(self, node)
        except Exception as exc:
            self.failure = exc
            await self.disengage()

    return guarded


def _pin_app_relations(configurations: list[Any], *, apps: list[App]) -> None:
    """Pin cross-App bindings using only this operation's already admitted Apps."""
    from app.services.relation_runtime import _app_matches_key

    by_id = {app.id: app for app in apps}
    pending = list(configurations)
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            if value.get("allow_cross_app") and value.get("target_app"):
                key = str(value["target_app"])
                resolution = str(value.get("resolution") or "workspace")
                if resolution.startswith("instance:"):
                    candidate = by_id.get(resolution[9:])
                    matches = (
                        [candidate]
                        if candidate and _app_matches_key(candidate, key)
                        else []
                    )
                elif resolution == "workspace":
                    matches = [app for app in apps if _app_matches_key(app, key)]
                else:
                    matches = []
                if len(matches) != 1:
                    raise BadRequestError(
                        message="Select the related Apps together in an App export. Each cross-App binding must identify one selected App."
                    )
                value["resolution"] = "instance:" + matches[0].id
            pending.extend(item for key, item in value.items() if key not in _TEXT_KEYS)


class ExportWalker(Walker):
    """Bound the containment walk and admit each resource before serializing it."""

    user_id: str
    workspace_id: str
    full: bool
    apps: list[Any] = Field(default_factory=list)
    tracks: list[Any] = Field(default_factory=list)
    entries: list[Any] = Field(default_factory=list)
    count: int = 0
    failure: Any = None

    async def admit(self, node: Any, kind: str) -> None:
        self.count += 1
        if self.count > MAX_NODES:
            raise BadRequestError(
                message="Selection exceeds the 10,000 item export limit; export smaller groups"
            )
        if kind != "entry" and node.workspace_id != self.workspace_id:
            raise InsufficientPermissionsError(
                message="Selected content is outside this workspace"
            )
        from app.services.resource_location import locate_resource

        location = await locate_resource(kind, node.id)
        if location is None or location.workspace_id != self.workspace_id:
            raise InsufficientPermissionsError(
                message="Selected content has an invalid workspace boundary"
            )
        await _authorize(self.user_id, kind, node.id)

    async def children(self, node: Any, kind: str) -> None:
        # limit+1 prevents truncation from masquerading as a complete backup.
        children = await node.nodes(
            edge=[CONTAINS], node=[kind], direction="out", limit=MAX_NODES + 1
        )
        if len(children) + self.count > MAX_NODES:
            raise BadRequestError(
                message="Selection exceeds the 10,000 item export limit"
            )
        await self.visit(children)

    @on_visit(App)
    @_fail_closed
    async def app(self, here: App) -> None:
        await self.admit(here, "app")
        self.apps.append(here)
        await self.children(here, "Track")

    @on_visit(Track)
    @_fail_closed
    async def track(self, here: Track) -> None:
        await self.admit(here, "track")
        self.tracks.append(here)
        if self.full:
            await self.children(here, "Entry")

    @on_visit(Entry)
    @_fail_closed
    async def entry(self, here: Entry) -> None:
        await self.admit(here, "entry")
        self.entries.append(here)


async def _bounded(node: Any, edge: Any, kind: str) -> list:
    rows = await node.nodes(
        edge=[edge], node=[kind], direction="out", limit=MAX_NODES + 1
    )
    if len(rows) > MAX_NODES:
        raise BadRequestError(message="Configuration exceeds the export limit")
    return rows


async def _export_app_package(app: App) -> tuple[PackagePayload, dict[str, bytes]]:
    """Resolve the installed bundle and pack it for a version-2 App archive."""
    from pathlib import Path

    from app.services.app_lifecycle import _resolve_app_bundle_dir
    from app.services.portable_package import (
        member_prefix_for_slug,
        pack_bundle_dir,
        package_meta_from_bundle,
    )

    bundle_dir = await _resolve_app_bundle_dir(app)
    if not bundle_dir:
        raise BadRequestError(
            message="Packaged App export requires its installed package on disk"
        )
    root = Path(bundle_dir)
    slug = str(
        getattr(app, "source_operational_model_slug", "")
        or getattr(app, "installed_package_slug", "")
        or root.name
    ).strip()
    if not slug:
        attached = await get_app_attached_operational_model(app)
        pkg = ((attached.manifest if attached else {}) or {}).get("package") or {}
        slug = str(pkg.get("slug") or pkg.get("name") or root.name).strip()
    from app.services.operational_model_loader import compute_bundle_fingerprint

    meta = package_meta_from_bundle(root, slug)
    packed, fingerprint = pack_bundle_dir(root, slug)
    current = compute_bundle_fingerprint(root)
    if current != fingerprint:
        raise BadRequestError(
            message="Installed package provenance does not match its export artifact"
        )
    expected = str(getattr(app, "installed_artifact_fingerprint", "") or "")
    # Accept empty stamps, current aligned stamps, or pre-alignment stamps that
    # included local cache/VCS junk (.pytest_cache, .DS_Store, …).
    legacy = compute_bundle_fingerprint(root, legacy=True)
    if expected and expected not in (current, legacy):
        raise BadRequestError(
            message="Installed package provenance does not match its export artifact"
        )
    return (
        PackagePayload(
            slug=slug,
            version=meta.get("version") or "",
            fingerprint=fingerprint,
            member_prefix=member_prefix_for_slug(slug),
        ),
        packed,
    )


async def export_resources(
    *, user_id: str, workspace_id: str, selection: ExportSelection
) -> tuple[bytes, list[str]]:
    """Export the selected authorized resources as a complete portable archive.

    Returns ``(zip_bytes, skipped_entries)`` where ``skipped_entries`` lists
    labels for full-data App records omitted because a required relation/file
    could not be kept in the archive.
    """
    if len(set(selection.ids)) != len(selection.ids):
        raise BadRequestError(message="Select each resource only once")
    walker = ExportWalker(
        user_id=user_id,
        workspace_id=workspace_id,
        full=selection.mode == "full",
        max_queue_size=MAX_NODES + 1,
        max_steps=MAX_NODES + 1,
    )
    for identity in selection.ids:
        node = await (App if selection.kind == "app" else Track).get(identity)
        if node is None:
            raise BadRequestError(message="Selected resource is unavailable")
        await walker.spawn(node)
        if walker.failure is not None:
            raise walker.failure
    package_by_slug: dict[str, PackagePayload] = {}
    package_files: dict[str, bytes] = {}
    app_package_slugs: dict[str, str] = {}
    track_package_allow: dict[str, bool] = {}
    if selection.kind == "app" and walker.apps:
        for app in walker.apps:
            cp = await get_app_attached_operational_model(app)
            if not manifest_has_runtime(cp.manifest if cp else {}):
                continue
            payload, packed = await _export_app_package(app)
            existing = package_by_slug.get(payload.slug)
            if existing is not None:
                if existing.fingerprint != payload.fingerprint:
                    raise BadRequestError(
                        message=(
                            f"Package {payload.slug!r} appears with conflicting contents "
                            "in the selected Apps"
                        )
                    )
            else:
                package_by_slug[payload.slug] = payload
                package_files.update(packed)
            app_package_slugs[app.id] = payload.slug
        for app in walker.apps:
            allow = app.id in app_package_slugs
            for child in await _bounded(app, CONTAINS, "Track"):
                track_package_allow[child.id] = allow
    package_payloads = list(package_by_slug.values())
    files: dict[str, bytes] = dict(package_files)
    tracks = {}
    for track in walker.tracks:
        cp = await get_track_attached_operational_model(track)
        if cp is None:
            raise BadRequestError(message="Track configuration is unavailable")
        types = await _bounded(cp, CONTAINS, "EntryType")
        tags = await _bounded(cp, CONTAINS, "Tag")
        if selection.kind == "track":
            parents = await track.nodes(
                edge=[CONTAINS], node=["WorkspaceApp"], direction="in", limit=2
            )
            if len(parents) > 1:
                raise BadRequestError(message="Track parent configuration is ambiguous")
            for parent in parents:
                parent_cp = await get_app_attached_operational_model(parent)
                if parent_cp:
                    inherited_tags = await _bounded(parent_cp, CONTAINS, "Tag")
                    if inherited_tags:
                        await _authorize(user_id, "app", parent.id)
                        tags += inherited_tags
            tags = list({tag.id: tag for tag in tags}.values())
        tag_refs = (
            {tag.id: f"{tag.id}:track:{track.id}" for tag in tags}
            if selection.kind == "track"
            else {}
        )
        from app.services.operational_model_graph import is_shared_track_profile

        config_refs = dict(tag_refs)
        if await is_shared_track_profile(cp):
            from app.services.entry_type_resolver import canonical_entry_type_key

            # Shared profiles retain template shells alongside the authoritative
            # per-Track materializations used by normal entry creation. Export
            # one definition per key, and map older shell-bound records to it.
            by_key: dict[str, EntryType] = {}
            type_aliases = {}
            seen_type_scopes = set()
            for entry_type in types:
                if entry_type.track_id and entry_type.track_id != track.id:
                    continue
                key = canonical_entry_type_key(entry_type)
                scope_key = (key, bool(entry_type.track_id))
                if scope_key in seen_type_scopes:
                    raise BadRequestError(
                        message="Track entry type configuration is ambiguous"
                    )
                seen_type_scopes.add(scope_key)
                previous = by_key.get(key)
                if previous is None:
                    by_key[key] = entry_type
                elif entry_type.track_id:
                    type_aliases[previous.id] = entry_type.id
                    by_key[key] = entry_type
                else:
                    type_aliases[entry_type.id] = previous.id
            types = list(by_key.values())
            # Snapshot each selected use of a shared template independently. Its
            # graph IDs cannot be repeated as archive identities across Tracks.
            config_refs.update(
                {node.id: f"{node.id}:track:{track.id}" for node in [cp, *types, *tags]}
            )
            config_refs.update(
                {source: config_refs[target] for source, target in type_aliases.items()}
            )
            tag_refs.update({tag.id: config_refs[tag.id] for tag in tags})
        views = await _bounded(track, HAS_VIEW, "View")
        rows = []
        for entry in walker.entries:
            if entry.track_id != track.id:
                continue
            attachments = []
            for attachment in await _bounded(entry, HAS_ATTACHMENT, "Attachment"):
                if attachment.scan_status not in {"clean", "skipped"}:
                    raise BadRequestError(
                        message="An attachment is not cleared for export"
                    )
                spec = PortableFile(
                    ref=attachment.id,
                    filename=attachment.filename,
                    mime_type=attachment.mime_type,
                    source_type=attachment.source_type,
                    external_url=attachment.external_url,
                )
                if attachment.source_type == "file":
                    content = await get_attachment_storage_service().read_attachment(
                        attachment.storage_key
                    )
                    if content is None:
                        raise BadRequestError(
                            message="An attachment file is missing; export was not created"
                        )
                    spec.member = (
                        f"files/{hashlib.sha256(attachment.id.encode()).hexdigest()}"
                    )
                    spec.sha256 = hashlib.sha256(content).hexdigest()
                    files[spec.member] = content
                    if sum(map(len, files.values())) > 50 * 1024 * 1024:
                        raise BadRequestError(
                            message="Attachment contents exceed the 50 MiB archive limit"
                        )
                attachments.append(spec)
            rows.append(
                PortableEntry(
                    ref=entry.id,
                    type_ref=config_refs.get(entry.type_id, entry.type_id),
                    title=entry.title,
                    body=entry.body,
                    status=entry.status,
                    created_at=entry.created_at,
                    updated_at=entry.updated_at,
                    custom_fields=entry.custom_fields,
                    tags=[tag_refs.get(ref, ref) for ref in entry.tags],
                    attachments=attachments,
                )
            )
        tracks[track.id] = PortableTrack(
            ref=track.id,
            model_ref=config_refs.get(cp.id, cp.id),
            title=track.title,
            purpose=track.purpose,
            icon=track.icon,
            accent_color=track.accent_color,
            relation_type_key=track.template_id or track.relation_type_key,
            nav_visible=track.nav_visible,
            search_index=track.search_index,
            manifest=_remap(
                _data_manifest(
                    cp.manifest or {},
                    allow_runtime=track_package_allow.get(track.id, False),
                ),
                config_refs,
            ),
            entry_types=[
                PortableType(
                    ref=config_refs.get(t.id, t.id),
                    name=t.name,
                    icon=t.icon,
                    form_schema=_remap(t.form_schema, config_refs),
                )
                for t in types
                if not t.track_id or t.track_id == track.id
            ],
            tags=[
                PortableTag(
                    ref=tag_refs.get(t.id, t.id),
                    name=t.name,
                    color=t.color,
                    group_key=t.group_key,
                    parent_ref=tag_refs.get(t.parent_tag_id, t.parent_tag_id),
                    aliases=t.aliases,
                    applies_to_entry_types=t.applies_to_entry_types,
                )
                for t in tags
                if not t.track_id or t.track_id == track.id
            ],
            views=[
                PortableView(
                    ref=v.id,
                    name=v.name,
                    type=v.type,
                    config=_remap(v.config, config_refs),
                    entry_type_keys=v.entry_type_keys,
                    default_entry_type_key=v.default_entry_type_key,
                    is_default=v.is_default,
                    hidden=v.hidden,
                )
                for v in views
            ],
            entries=rows,
        )
    apps = []
    for app in walker.apps:
        cp = await get_app_attached_operational_model(app)
        children = await _bounded(app, CONTAINS, "Track")
        from app.services.dashboard_service import list_dashboards

        dashboards = await list_dashboards(user_id=user_id, app_id=app.id)
        app_tags = await _bounded(cp, CONTAINS, "Tag") if cp else []
        package_slug = app_package_slugs.get(app.id, "")
        apps.append(
            PortableApp(
                tags=[
                    PortableTag(
                        ref=t.id,
                        name=t.name,
                        color=t.color,
                        group_key=t.group_key,
                        parent_ref=t.parent_tag_id,
                        aliases=t.aliases,
                        applies_to_entry_types=t.applies_to_entry_types,
                    )
                    for t in app_tags
                ],
                dashboards=[
                    PortableDashboard(
                        ref=d.id,
                        name=d.name,
                        layout=d.layout,
                        widgets=d.widgets,
                        is_default=d.is_default,
                    )
                    for d in dashboards
                ],
                ref=app.id,
                model_ref=cp.id if cp else "",
                name=app.name,
                description=app.description,
                icon=app.icon,
                accent_color=app.accent_color,
                manifest=_data_manifest(
                    cp.manifest if cp else {},
                    allow_runtime=bool(package_slug),
                ),
                settings=_export_settings(
                    app.settings or {}, app.settings_schema or {}
                ),
                package_slug=package_slug,
                home_composition=app.home_composition,
                tracks=[tracks[t.id] for t in children],
            )
        )
    archive = PortableArchive(
        version=2 if package_payloads else 1,
        mode=selection.mode,
        kind=selection.kind,
        apps=apps,
        tracks=list(tracks.values()) if selection.kind == "track" else [],
        packages=package_payloads,
    )
    configurations = [a.manifest for a in archive.apps]
    for t in _tracks(archive):
        configurations.append(t.manifest)
        configurations.extend(et.form_schema for et in t.entry_types)
    await asyncio.to_thread(_pin_app_relations, configurations, apps=list(walker.apps))
    skipped_entries: list[str] = []
    if archive.mode == "full" and archive.kind == "app":
        archive, skipped_entries = await _sanitize_dangling_export_refs(archive, files)
    validate_archive(archive, files)
    content = await asyncio.to_thread(encode_archive, archive.wire_dict(), files)
    return content, skipped_entries


def _tracks(archive: PortableArchive) -> list[PortableTrack]:
    return archive.tracks + [track for app in archive.apps for track in app.tracks]


_OUTSIDE_EXPORT_PREFIX = (
    "A record references content outside this export. "
    "Include the related Apps or Tracks in the same export."
)


def _record_label(entry: PortableEntry) -> str:
    title = str(entry.title or "").strip()
    return title or entry.ref


async def _describe_live_relation_target(target_id: str, *, kind: str) -> str | None:
    """Return a human location for a live target, or None if it no longer exists."""
    if kind == "track":
        track = await Track.get(target_id)
        if track is None:
            return None
        parents = await track.nodes(
            edge=[CONTAINS], node=["WorkspaceApp"], direction="in", limit=2
        )
        track_title = str(getattr(track, "title", "") or "").strip() or track.id
        if parents:
            app_name = (
                str(getattr(parents[0], "name", "") or "").strip() or parents[0].id
            )
            return f"App {app_name!r} / Track {track_title!r}"
        return f"Track {track_title!r}"

    target = await Entry.get(target_id)
    if target is None:
        return None
    track = await Track.get(target.track_id) if target.track_id else None
    track_title = (
        str(getattr(track, "title", "") or "").strip()
        if track is not None
        else str(target.track_id or "unknown Track")
    ) or "unknown Track"
    if track is None:
        return f"Track {track_title!r}"
    parents = await track.nodes(
        edge=[CONTAINS], node=["WorkspaceApp"], direction="in", limit=2
    )
    if parents:
        app_name = str(getattr(parents[0], "name", "") or "").strip() or parents[0].id
        return f"App {app_name!r} / Track {track_title!r}"
    return f"Track {track_title!r}"


def _field_is_required(field: dict) -> bool:
    return bool(field.get("required"))


def _relation_or_file_base(field: dict, manifest: dict | None) -> str | None:
    base = _field_base(field, manifest)
    if base in {"relation", "multi_relation", "relations", "file", "files"}:
        return base
    return None


def _required_relation_file_gap(
    item: PortableEntry,
    entry_type: PortableType,
    mapping: dict[str, str],
    *,
    skip_refs: set[str] | None = None,
    manifest: dict | None = None,
) -> str | None:
    """Return the field key when a required relation/file cannot be remapped."""
    skipped = skip_refs or set()
    for field in entry_type.form_schema.get("fields", []) or []:
        if not isinstance(field, dict) or not _field_is_required(field):
            continue
        key = field.get("key")
        base = _relation_or_file_base(field, manifest)
        if not key or base is None:
            continue
        value = (item.custom_fields or {}).get(key)
        raw_values = value if isinstance(value, list) else ([value] if value else [])
        if not raw_values:
            return str(key)
        for raw in raw_values:
            if not isinstance(raw, str) or not raw.strip():
                return str(key)
            if raw in skipped or raw not in mapping:
                return str(key)
    return None


async def _drop_reserved_import_entry(
    *,
    item: PortableEntry,
    mapping: dict[str, str],
    stored_keys: list[str],
) -> None:
    """Remove a placeholder entry (and its restored files) that will not be finalized."""
    entry_id = mapping.pop(item.ref, None)
    for attachment in item.attachments:
        mapping.pop(attachment.ref, None)
    if entry_id:
        entry = await Entry.get(entry_id)
        if entry is not None:
            for attachment in await _bounded(entry, HAS_ATTACHMENT, "Attachment"):
                keys = [attachment.storage_key, attachment.thumb_storage_key]
                await entry.disconnect(attachment)
                await attachment.delete()
                for key in keys:
                    if key and key in stored_keys:
                        await get_attachment_storage_service().delete_attachment(key)
                        stored_keys.remove(key)
            await entry.delete()


def _collect_incomplete_import_refs(
    track_pairs: list[tuple[PortableTrack, Track]],
    mapping: dict[str, str],
) -> set[str]:
    """Cascade-skip archive entries whose required relation/file targets are unavailable."""
    skip_refs: set[str] = set()
    changed = True
    while changed:
        changed = False
        for track_spec, _track in track_pairs:
            for item in track_spec.entries:
                if item.ref in skip_refs:
                    continue
                et = next(
                    row for row in track_spec.entry_types if row.ref == item.type_ref
                )
                if _required_relation_file_gap(
                    item,
                    et,
                    mapping,
                    skip_refs=skip_refs,
                    manifest=track_spec.manifest,
                ):
                    skip_refs.add(item.ref)
                    changed = True
    return skip_refs


async def _sanitize_dangling_export_refs(
    archive: PortableArchive, files: dict[str, bytes]
) -> tuple[PortableArchive, list[str]]:
    """Drop deleted relation/file ids; omit rows that lose required links; refuse live outs.

    Incomplete required-link rows are omitted and returned as labels for the
    caller to report. Live targets outside the selection still fail closed.
    """
    track_refs = {track.ref for track in _tracks(archive)}
    omitted: set[str] = set()
    omitted_labels: dict[str, str] = {}

    async def clean_entry(
        entry: PortableEntry,
        track: PortableTrack,
        entry_refs: set[str],
    ) -> PortableEntry | None:
        """Return cleaned entry, or None to omit it from the archive."""
        if entry.ref in omitted:
            return None
        et = next(row for row in track.entry_types if row.ref == entry.type_ref)
        fields = deepcopy(entry.custom_fields or {})
        attachment_refs = {attachment.ref for attachment in entry.attachments}
        for field in et.form_schema.get("fields", []) or []:
            if not isinstance(field, dict):
                continue
            key = field.get("key")
            base = _relation_or_file_base(field, track.manifest)
            if not key or base is None:
                continue
            if key not in fields:
                if _field_is_required(field):
                    return None
                continue
            value = fields.get(key)
            many = isinstance(value, list)
            raw_values = value if many else ([value] if value else [])
            kept: list[Any] = []
            for item in raw_values:
                if not isinstance(item, str) or not item:
                    continue
                if base in {"relation", "multi_relation", "relations"}:
                    target_kind = (field.get("relation") or {}).get("target", "entry")
                    allowed = entry_refs if target_kind == "entry" else track_refs
                    if item in allowed:
                        kept.append(item)
                        continue
                    if item in omitted:
                        continue
                    where = await _describe_live_relation_target(
                        item, kind=str(target_kind or "entry")
                    )
                    if where is None:
                        continue
                    raise BadRequestError(
                        message=(
                            f"{_OUTSIDE_EXPORT_PREFIX} Record {_record_label(entry)!r} "
                            f"field {key!r} references {item!r} on {where}."
                        )
                    )
                if item in attachment_refs:
                    kept.append(item)
                    continue
                attachment = await Attachment.get(item)
                if attachment is None:
                    continue
                raise BadRequestError(
                    message=(
                        f"{_OUTSIDE_EXPORT_PREFIX} Record {_record_label(entry)!r} "
                        f"field {key!r} references attachment {item!r} that is not "
                        "included with this record."
                    )
                )
            if many:
                fields[key] = kept
            elif kept:
                fields[key] = kept[0]
            else:
                fields.pop(key, None)
            if _field_is_required(field):
                current = fields.get(key)
                empty = current is None or current == "" or current == []
                if empty:
                    return None
        return entry.model_copy(update={"custom_fields": fields})

    working = archive
    while True:
        entry_refs = {
            entry.ref
            for track in _tracks(working)
            for entry in track.entries
            if entry.ref not in omitted
        }
        newly_omitted: set[str] = set()
        apps: list[PortableApp] = []
        for app in working.apps:
            tracks: list[PortableTrack] = []
            for track in app.tracks:
                kept_entries: list[PortableEntry] = []
                for entry in track.entries:
                    if entry.ref in omitted:
                        continue
                    cleaned = await clean_entry(entry, track, entry_refs)
                    if cleaned is None:
                        newly_omitted.add(entry.ref)
                        omitted_labels[entry.ref] = _record_label(entry)
                        for attachment in entry.attachments:
                            if attachment.member:
                                files.pop(attachment.member, None)
                        continue
                    kept_entries.append(cleaned)
                tracks.append(track.model_copy(update={"entries": kept_entries}))
            apps.append(app.model_copy(update={"tracks": tracks}))
        working = working.model_copy(update={"apps": apps})
        if not newly_omitted:
            break
        omitted.update(newly_omitted)
    labels = [
        omitted_labels[ref]
        for ref in sorted(omitted, key=lambda r: omitted_labels.get(r, r))
    ]
    return working, labels


def validate_archive(archive: PortableArchive, files: dict[str, bytes]) -> None:
    """Validate all identities/files/references before the first restore write."""
    if (archive.kind == "app" and (not archive.apps or archive.tracks)) or (
        archive.kind == "track" and (not archive.tracks or archive.apps)
    ):
        raise BadRequestError(message="Archive resource selection is invalid")
    if archive.packages:
        from app.services.portable_package import validate_package_members

        for payload in archive.packages:
            validate_package_members(payload, files)
    track_allow_runtime: dict[str, bool] = {
        track.ref: False for track in archive.tracks
    }
    refs = [app.ref for app in archive.apps]
    refs.extend(app.model_ref for app in archive.apps if app.model_ref)
    entries = set()
    app_tag_refs = {t.ref for app in archive.apps for t in app.tags}
    members = set()
    for app in archive.apps:
        allow_runtime = bool(app.package_slug)
        refs.extend(d.ref for d in app.dashboards)
        refs.extend(t.ref for t in app.tags)
        _data_manifest(app.manifest, allow_runtime=allow_runtime)
        for track in app.tracks:
            track_allow_runtime[track.ref] = allow_runtime
    for track in _tracks(archive):
        allow_runtime = track_allow_runtime.get(track.ref, False)
        _data_manifest(track.manifest, allow_runtime=allow_runtime)
        from app.services.operational_model_compile import (
            compile_canonical_manifest,
            slug_manifest_key,
        )

        if track.manifest:
            compile_canonical_manifest(
                manifest=_data_manifest(track.manifest, allow_runtime=allow_runtime)
            )
        declarations = []
        for item in track.entry_types:
            form = item.form_schema
            fields = form.get("fields", [])
            if not isinstance(fields, list) or any(
                not isinstance(field, dict) for field in fields
            ):
                raise BadRequestError(message="Invalid archive entry field definitions")
            declarations.append(
                {
                    "key": form.get("_manifest_entry_type_key")
                    or slug_manifest_key(item.name),
                    "name": item.name,
                    "icon": item.icon,
                    "form_schema": form,
                    "fields": fields,
                }
            )
        compile_canonical_manifest(
            manifest={
                "operational_model_schema_version": 2,
                "scope": "track",
                "track": {"entry_types": declarations},
                "field_types": track.manifest.get("field_types", []),
                "view_types": track.manifest.get("view_types", []),
            }
        )
        refs.append(track.ref)
        if track.model_ref:
            refs.append(track.model_ref)
        refs.extend(t.ref for t in track.entry_types + track.tags + track.views)
        type_refs = {t.ref for t in track.entry_types}
        tag_refs = {t.ref for t in track.tags} | app_tag_refs
        if not type_refs:
            raise BadRequestError(
                message="Every Track must contain at least one entry type"
            )
        for tag in track.tags:
            if tag.parent_ref and tag.parent_ref not in tag_refs:
                raise BadRequestError(message="A tag parent is outside the archive")
        for entry in track.entries:
            refs.append(entry.ref)
            entries.add(entry.ref)
            if entry.type_ref not in type_refs or not set(entry.tags) <= tag_refs:
                raise BadRequestError(message="An entry has an unavailable type or tag")
            for attachment in entry.attachments:
                refs.append(attachment.ref)
                if attachment.source_type == "file":
                    content = files.get(attachment.member)
                    if (
                        content is None
                        or not str(attachment.member).startswith("files/")
                        or hashlib.sha256(content).hexdigest() != attachment.sha256
                    ):
                        raise BadRequestError(
                            message="Archive attachment is missing or its checksum does not match"
                        )
                    members.add(attachment.member)
                elif not attachment.external_url:
                    raise BadRequestError(
                        message="URL attachment is missing its address"
                    )
        if archive.mode == "configuration" and track.entries:
            raise BadRequestError(
                message="Configuration archives cannot contain records"
            )
    all_tags = [t for app in archive.apps for t in app.tags] + [
        t for track in _tracks(archive) for t in track.tags
    ]
    parents = {tag.ref: tag.parent_ref for tag in all_tags}
    complete = set()
    for start in parents:
        current = start
        path = set()
        while current and current not in complete:
            if current in path or current not in parents:
                raise BadRequestError(message="Invalid tag hierarchy in archive")
            path.add(current)
            current = parents[current]
        complete.update(path)
    if (
        len(refs) > MAX_NODES
        or len(refs) != len(set(refs))
        or any(not ref for ref in refs)
    ):
        raise BadRequestError(
            message="Archive has duplicate identities or exceeds the item limit"
        )
    configurations: list[Any] = []
    for app in archive.apps:
        configurations.extend([app.manifest, app.home_composition])
        configurations.extend(d.widgets for d in app.dashboards)
    for track in _tracks(archive):
        configurations.extend([track.manifest, track.search_index])
        configurations.extend(t.form_schema for t in track.entry_types)
        configurations.extend(v.config for v in track.views)
    known = set(refs)
    while configurations:
        value = configurations.pop()
        if isinstance(value, dict):
            configurations.extend(
                item for key, item in value.items() if key not in _TEXT_KEYS
            )
        elif isinstance(value, list):
            configurations.extend(value)
        elif (
            isinstance(value, str)
            and value.startswith("instance:")
            and value[9:] not in known
        ):
            raise BadRequestError(
                message="A relation targets an App outside this archive"
            )
        elif (
            isinstance(value, str)
            and value.startswith(
                (
                    "n.Track.",
                    "n.WorkspaceApp.",
                    "n.Entry.",
                    "n.Tag.",
                    "n.EntryType.",
                    "n.View.",
                    "n.Dashboard.",
                    "n.OperationalModel.",
                )
            )
            and value not in known
        ):
            raise BadRequestError(
                message="A saved configuration references content outside this export. Include the related resources together."
            )
    package_members = {
        name
        for name in files
        if any(name.startswith(payload.member_prefix) for payload in archive.packages)
    }
    if set(files) - package_members != members:
        raise BadRequestError(message="Archive contains unreferenced files")
    for track in _tracks(archive):
        for entry in track.entries:
            et = next(t for t in track.entry_types if t.ref == entry.type_ref)
            for field in et.form_schema.get("fields", []):
                base = _field_base(field, track.manifest)
                if base not in {
                    "relation",
                    "multi_relation",
                    "relations",
                    "file",
                    "files",
                }:
                    continue
                value = entry.custom_fields.get(field.get("key"))
                values = value if isinstance(value, list) else [value] if value else []
                if base in {"relation", "multi_relation", "relations"}:
                    target = (field.get("relation") or {}).get("target", "entry")
                    allowed = (
                        entries
                        if target == "entry"
                        else (
                            {t.ref for t in _tracks(archive)}
                            if target == "track"
                            else set()
                        )
                    )
                else:
                    allowed = {a.ref for a in entry.attachments}
                bad = next(
                    (
                        item
                        for item in values
                        if not isinstance(item, str) or item not in allowed
                    ),
                    None,
                )
                if bad is not None:
                    key = field.get("key")
                    raise BadRequestError(
                        message=(
                            f"{_OUTSIDE_EXPORT_PREFIX} Record {_record_label(entry)!r} "
                            f"field {key!r} references {bad!r}."
                        )
                    )


async def read_archive(data: bytes) -> tuple[PortableArchive, dict[str, bytes]]:
    """Decode and fully preflight untrusted archive content off the event loop."""
    raw, files = await asyncio.to_thread(decode_archive, data)
    try:
        archive = PortableArchive.model_validate(raw)
        await asyncio.to_thread(validate_archive, archive, files)
    except (ValidationError, TypeError, AttributeError, ValueError) as exc:
        raise BadRequestError(message="Invalid portable archive configuration") from exc
    return archive, files


def archive_summary(archive: PortableArchive) -> dict[str, Any]:
    """Return a content preview without exposing record bodies."""
    tracks = _tracks(archive)
    summary: dict[str, Any] = {
        "kind": archive.kind,
        "mode": archive.mode,
        "apps": len(archive.apps),
        "tracks": len(tracks),
        "entries": sum(len(t.entries) for t in tracks),
        "attachments": sum(len(e.attachments) for t in tracks for e in t.entries),
        "names": [a.name for a in archive.apps] or [t.title for t in tracks],
    }
    if archive.packages:
        packages = [
            {
                "slug": payload.slug,
                "version": payload.version,
                "installs_package": True,
            }
            for payload in archive.packages
        ]
        summary["packages"] = packages
        # Keep singular package for single-payload archives (legacy UI).
        if len(packages) == 1:
            summary["package"] = packages[0]
    return summary


class _CleanupWalker(Walker):
    """Follow only the new resources' owned subtree, never author/grant edges."""

    nodes: list[Any] = Field(default_factory=list)
    seen: set[str] = Field(default_factory=set)
    failure: Any = None

    @on_visit(Node)
    @_fail_closed
    async def collect(self, here: Any) -> None:
        from jvspatial.core import Edge

        if here.id in self.seen:
            return
        self.seen.add(here.id)
        self.nodes.append(here)
        # Traversal is limited to configuration/containment classes. Relations,
        # authorship and grants cannot draw an existing workspace into cleanup.
        children = await here.nodes(
            edge=[Edge, CONTAINS, HAS_VIEW, HAS_ATTACHMENT],
            node=[
                "OperationalModel",
                "Views",
                "View",
                "EntryType",
                "Tag",
                "Track",
                "Entry",
                "Attachment",
                "Dashboards",
                "Dashboard",
                "ApplicationDefinition",
            ],
            direction="out",
            limit=MAX_NODES + 1,
        )
        from app.models.edges import (
            CATALOGS,
            DEFINES_TRACK_PROFILE,
            HAS_APPLICATION_DEFINITION,
            HAS_OPERATIONAL_MODEL,
        )

        for edge in (
            HAS_OPERATIONAL_MODEL,
            CATALOGS,
            HAS_APPLICATION_DEFINITION,
            DEFINES_TRACK_PROFILE,
        ):
            children += await here.nodes(
                edge=[edge],
                node=["OperationalModel", "View", "Dashboard", "ApplicationDefinition"],
                direction="out",
                limit=MAX_NODES + 1,
            )
        await self.visit(children)


async def _cleanup_import_attempt(roots, stored_keys, workspace_id, atomic):
    """Compensate only attempt-owned roots; continue after individual failures."""
    from anyio import CancelScope

    failures = []
    with CancelScope(shield=True):
        for root in reversed(roots):
            if isinstance(root, App) and root.installed_package_slug:
                from app.agentive.services.skill_registry import (
                    unregister_skills_for_app,
                )
                from app.agentive.services.uplink_registry import (
                    unregister_app_agents_for_app,
                )
                from app.services.hooks.install_hook import (
                    unregister_bundle_on_uninstall,
                )

                try:
                    await unregister_bundle_on_uninstall(
                        workspace_id, root.installed_package_slug, app_id=root.id
                    )
                except Exception as exc:
                    failures.append(exc)
                if not atomic:
                    for cleanup in (
                        unregister_app_agents_for_app,
                        unregister_skills_for_app,
                    ):
                        try:
                            await cleanup(root.id, strict=True)
                        except Exception as exc:
                            failures.append(exc)
            if not atomic:
                cleanup = _CleanupWalker(
                    max_queue_size=MAX_NODES + 1, max_steps=MAX_NODES * 4
                )
                try:
                    if await type(root).get(root.id) is None:
                        continue
                    await cleanup.spawn(root)
                    if cleanup.failure is not None:
                        raise cleanup.failure
                except Exception as exc:
                    failures.append(exc)
                for node in reversed(cleanup.nodes):
                    try:
                        await node.delete()
                    except Exception as exc:
                        failures.append(exc)
        if any(isinstance(root, App) for root in roots):
            try:
                from app.agentive.workspace_agent_profile import (
                    invalidate_workspace_profile,
                )
                from app.services.capability_catalogue import (
                    compile_workspace_catalogue,
                )

                await compile_workspace_catalogue(workspace_id, activate=True)
                invalidate_workspace_profile(workspace_id)
            except Exception as exc:
                failures.append(exc)
        for key in set(stored_keys):
            try:
                await get_attachment_storage_service().delete_attachment(key)
            except Exception as exc:
                failures.append(exc)
    if failures:
        raise ExceptionGroup("Portable import cleanup is incomplete", failures)


async def _available_name(
    model: Any, field: str, value: str, workspace_id: str, app: App | None = None
) -> str:
    for attempt in range(1000):
        name = value if attempt == 0 else f"{value[:180]} (import {attempt})"
        if app:
            siblings = await _bounded(app, CONTAINS, "Track")
            conflict = any(t.title_fold == compute_fold(name) for t in siblings)
        else:
            conflict = await model.find_one(
                {
                    "context.workspace_id": workspace_id,
                    f"context.{field}_fold": compute_fold(name),
                }
            )
        if not conflict:
            return name
    raise BadRequestError(message="Too many copies with the same name")


async def _restore_config(
    track: Track,
    spec: PortableTrack,
    mapping: dict[str, str],
    *,
    allow_runtime: bool = False,
) -> None:
    from app.services.operational_model_runtime import sync_attached_manifest

    cp = await get_track_attached_operational_model(track)
    if cp is None:
        raise BadRequestError(message="New Track configuration is unavailable")
    mapping[spec.ref] = track.id
    if spec.model_ref:
        mapping[spec.model_ref] = cp.id
    # These defaults belong exclusively to this newly created private Track.
    for old in await _bounded(cp, CONTAINS, "EntryType") + await _bounded(
        track, HAS_VIEW, "View"
    ):
        await old.delete()
    for item in spec.entry_types:
        node = await EntryType.create(
            name=item.name,
            name_fold=compute_fold(item.name),
            icon=item.icon,
            track_id=track.id,
            form_schema=item.form_schema,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        await cp.connect(node, edge=CONTAINS)
        mapping[item.ref] = node.id
    for item in spec.tags:
        tag = await Tag.create(
            name=item.name,
            name_fold=compute_fold(item.name),
            color=item.color,
            track_id=track.id,
            group_key=item.group_key,
            aliases=item.aliases,
            applies_to_entry_types=item.applies_to_entry_types,
        )
        await cp.connect(tag, edge=CONTAINS)
        mapping[item.ref] = tag.id
    for item in spec.tags:
        if item.parent_ref:
            tag = await Tag.get(mapping[item.ref])
            tag.parent_tag_id = mapping[item.parent_ref]
            await tag.save()
    # Config/view bindings are remapped after every resource identity is known.
    for item in spec.views:
        view = await create_track_view(
            track,
            name=item.name,
            name_fold=compute_fold(item.name),
            type=item.type,
            config=item.config,
            entry_type_keys=item.entry_type_keys,
            default_entry_type_key=item.default_entry_type_key,
            is_default=item.is_default,
            hidden=item.hidden,
            created_at=utc_now_iso(),
        )
        mapping[item.ref] = view.id
    track.relation_type_key = spec.relation_type_key
    track.nav_visible = spec.nav_visible
    track.search_index = spec.search_index
    await track.save()
    source_manifest = _data_manifest(spec.manifest, allow_runtime=allow_runtime)
    cp.manifest = source_manifest
    await sync_attached_manifest(cp)
    # Sync rebuilds graph-owned definitions. Retain declarative Track options
    # (for example skills) that have no separate materialized nodes.
    cp.manifest = {
        **source_manifest,
        **cp.manifest,
        "track": {
            **(source_manifest.get("track") or {}),
            **(cp.manifest.get("track") or {}),
        },
    }
    from app.services.operational_model_compile import compile_canonical_manifest

    await asyncio.to_thread(compile_canonical_manifest, manifest=cp.manifest)
    await cp.save()


async def _restore_file(
    entry: Entry,
    spec: PortableFile,
    files: dict[str, bytes],
    user_id: str,
    stored_keys: list[str],
) -> Attachment:
    from app.services.attachment_scanner import get_attachment_scanner
    from app.services.attachment_upload_shared import resolve_effective_mime
    from app.services.url_safety import validate_public_http_url

    content = files.get(spec.member, b"")
    from app.config import settings

    if len(content) > settings.ATTACHMENT_MAX_UPLOAD_BYTES:
        raise BadRequestError(
            message="An archive file exceeds this installation's attachment limit"
        )
    if spec.source_type == "file":
        mime = await asyncio.to_thread(
            resolve_effective_mime,
            head_bytes=content[:8192],
            content_type_claim=spec.mime_type,
            filename=spec.filename,
        )
        verdict = await get_attachment_scanner().scan(
            content=content, mime_type=mime, filename=spec.filename
        )
        if verdict.is_blocked or verdict.status not in {"clean", "skipped"}:
            raise BadRequestError(
                message="An imported file did not pass the attachment scanner"
            )
    else:
        await validate_public_http_url(spec.external_url)
        mime = "text/uri-list"
        verdict = None
    attachment = await Attachment.create(
        filename=spec.filename,
        mime_type=mime,
        source_type=spec.source_type,
        size=len(content),
        external_url=spec.external_url if spec.source_type == "url" else "",
        uploaded_by=user_id,
        content_hash=spec.sha256,
        scan_status=verdict.status if verdict else "skipped",
        scan_engine=verdict.engine if verdict else "n/a",
        metadata_status="pending" if verdict else "skipped",
        created_at=utc_now_iso(),
    )
    await entry.connect(
        attachment, edge=HAS_ATTACHMENT, attached_by=user_id, attached_at=utc_now_iso()
    )
    if spec.source_type == "file":
        storage = get_attachment_storage_service()
        stored_keys.append(
            storage.build_storage_key(entry.id, attachment.id, attachment.filename)
        )
        stored = await storage.save_attachment(
            entry_id=entry.id,
            attachment_id=attachment.id,
            filename=attachment.filename,
            content=content,
            mime_type=mime,
        )
        attachment.storage_key = str(stored.get("path") or "")
        stored_keys.append(attachment.storage_key)
        from app.services.attachment_thumbnails import THUMB_FILENAME, ensure_thumbnail

        stored_keys.append(
            storage.build_sibling_key(entry.id, attachment.id, THUMB_FILENAME)
        )
        thumbnail = await ensure_thumbnail(
            attachment,
            entry_id=entry.id,
            content=content,
            storage=get_attachment_storage_service(),
        )
        if thumbnail:
            stored_keys.append(thumbnail)
        await attachment.save()
    return attachment


def _track_match_key(track: Any) -> str:
    relation = str(
        getattr(track, "relation_type_key", "")
        or getattr(track, "template_id", "")
        or ""
    ).strip()
    if relation:
        return f"key:{relation}"
    title = str(getattr(track, "title", "") or "").strip()
    return f"title:{compute_fold(title)}" if title else ""


def _portable_track_match_key(spec: PortableTrack) -> str:
    relation = str(spec.relation_type_key or "").strip()
    if relation:
        return f"key:{relation}"
    return f"title:{compute_fold(spec.title)}" if spec.title else ""


def _order_archive_apps(apps: list[PortableApp]) -> list[PortableApp]:
    """Topologically order archive Apps by hard ``requires_apps`` within the ZIP."""
    if len(apps) <= 1:
        return list(apps)
    from app.services.app_batch_install import _topo_sort

    alias_to_ref: dict[str, str] = {}
    ambiguous_aliases: set[str] = set()
    for app in apps:
        aliases: set[str] = {app.name, app.name.casefold()}
        if app.package_slug:
            aliases.add(app.package_slug)
        pkg = (app.manifest or {}).get("package") or {}
        for key in (pkg.get("slug"), pkg.get("name"), pkg.get("key")):
            if key:
                aliases.add(str(key).strip())
        app_key = ((app.manifest or {}).get("app") or {}).get("key")
        if app_key:
            aliases.add(str(app_key).strip())
        from app.services.operational_model_compile import slug_manifest_key

        aliases |= {slug_manifest_key(alias) for alias in aliases}
        aliases |= {alias.casefold() for alias in aliases}
        for alias in aliases:
            if alias in alias_to_ref and alias_to_ref[alias] != app.ref:
                ambiguous_aliases.add(alias)
            if alias:
                alias_to_ref[alias] = app.ref

    nodes = [app.ref for app in apps]
    edges: dict[str, list[str]] = {}
    for app in apps:
        deps: list[str] = []
        for dep in ((app.manifest or {}).get("app") or {}).get("requires_apps") or []:
            if not isinstance(dep, dict) or dep.get("optional"):
                continue
            key = str(dep.get("key") or "").strip()
            if not key:
                continue
            # Only order against Apps present in this archive; install_app still
            # enforces hard deps that must already exist in the destination.
            if any(
                alias in ambiguous_aliases
                for alias in (key, key.casefold(), slug_manifest_key(key))
            ):
                raise BadRequestError(
                    message="Archive App dependency aliases are ambiguous"
                )
            target = (
                alias_to_ref.get(key)
                or alias_to_ref.get(key.casefold())
                or alias_to_ref.get(slug_manifest_key(key))
            )
            if target and target != app.ref:
                deps.append(target)
        edges[app.ref] = deps
    order, cycle = _topo_sort(nodes, edges)
    if cycle:
        raise BadRequestError(
            message="Apps in this archive have a circular dependency and cannot be imported"
        )
    by_ref = {app.ref: app for app in apps}
    return [by_ref[ref] for ref in order]


async def _overlay_installed_packaged_tracks(
    *,
    user_id: str,
    workspace_id: str,
    app: App,
    spec: PortableApp,
    mapping: dict[str, str],
    events: list[dict],
) -> list[tuple[PortableTrack, Track]]:
    """Match installed Tracks to archive specs; create blanks for extras."""
    from app.services.track_service import create_track_in_space

    installed_tracks = await _bounded(app, CONTAINS, "Track")
    by_key: dict[str, list[Track]] = {}
    for track in installed_tracks:
        if key := _track_match_key(track):
            by_key.setdefault(key, []).append(track)
    track_pairs: list[tuple[PortableTrack, Track]] = []
    for track_spec in spec.tracks:
        key = _portable_track_match_key(track_spec)
        candidates = by_key.get(key, [])
        track = next(
            (
                row
                for row in candidates
                if compute_fold(row.title) == compute_fold(track_spec.title)
            ),
            candidates[0] if candidates else None,
        )
        if track is not None:
            candidates.remove(track)
        if track is None:
            track = await create_track_in_space(
                user_id,
                title=await _available_name(
                    Track, "title", track_spec.title, workspace_id, app
                ),
                purpose=track_spec.purpose,
                icon=track_spec.icon,
                accent_color=track_spec.accent_color,
                workspace_id=workspace_id,
                app_id=app.id,
                visibility="private",
                change_event_sink=events.append,
            )
            await _restore_config(track, track_spec, mapping, allow_runtime=True)
        else:
            track.title = track_spec.title
            track.title_fold = compute_fold(track_spec.title)
            track.purpose = track_spec.purpose
            track.icon = track_spec.icon
            track.accent_color = track_spec.accent_color
            track.nav_visible = track_spec.nav_visible
            track.search_index = track_spec.search_index
            await track.save()
            mapping[track_spec.ref] = track.id
            cp = await get_track_attached_operational_model(track)
            if cp is None:
                raise BadRequestError(
                    message="Installed Track configuration is unavailable"
                )
            if track_spec.model_ref:
                mapping[track_spec.model_ref] = cp.id
            types = await _bounded(cp, CONTAINS, "EntryType") if cp else []
            for item in track_spec.entry_types:
                match = next(
                    (
                        row
                        for row in types
                        if compute_fold(row.name) == compute_fold(item.name)
                    ),
                    None,
                )
                if match is not None:
                    mapping[item.ref] = match.id
                else:
                    node = await EntryType.create(
                        name=item.name,
                        name_fold=compute_fold(item.name),
                        icon=item.icon,
                        track_id=track.id,
                        form_schema=item.form_schema,
                        created_at=utc_now_iso(),
                        updated_at=utc_now_iso(),
                    )
                    if cp is not None:
                        await cp.connect(node, edge=CONTAINS)
                    mapping[item.ref] = node.id
            existing_tags = await _bounded(cp, CONTAINS, "Tag")
            for item in track_spec.tags:
                tag = next(
                    (
                        row
                        for row in existing_tags
                        if compute_fold(row.name) == compute_fold(item.name)
                    ),
                    None,
                )
                if tag is None:
                    tag = await Tag.create(
                        name=item.name,
                        name_fold=compute_fold(item.name),
                        track_id=track.id,
                    )
                    await cp.connect(tag, edge=CONTAINS)
                tag.color = item.color
                tag.group_key = item.group_key
                tag.aliases = item.aliases
                tag.applies_to_entry_types = item.applies_to_entry_types
                await tag.save()
                mapping[item.ref] = tag.id
            for item in track_spec.tags:
                if item.parent_ref and item.parent_ref in mapping:
                    tag = await Tag.get(mapping[item.ref])
                    tag.parent_tag_id = mapping[item.parent_ref]
                    await tag.save()
            existing_views = await _bounded(track, HAS_VIEW, "View")
            for item in track_spec.views:
                view = next(
                    (
                        row
                        for row in existing_views
                        if compute_fold(row.name) == compute_fold(item.name)
                    ),
                    None,
                )
                if view is None:
                    view = await create_track_view(
                        track,
                        name=item.name,
                        name_fold=compute_fold(item.name),
                        type=item.type,
                        config=item.config,
                        entry_type_keys=item.entry_type_keys,
                        default_entry_type_key=item.default_entry_type_key,
                        is_default=item.is_default,
                        hidden=item.hidden,
                        created_at=utc_now_iso(),
                    )
                view.type = item.type
                view.entry_type_keys = item.entry_type_keys
                view.default_entry_type_key = item.default_entry_type_key
                view.is_default = item.is_default
                view.hidden = item.hidden
                await view.save()
                mapping[item.ref] = view.id
        track_pairs.append((track_spec, track))
    return track_pairs


async def _import_packaged_archive(
    *,
    user_id: str,
    workspace_id: str,
    archive: PortableArchive,
    files: dict[str, bytes],
) -> dict:
    """Resolve admitted packages, install fresh Apps in dependency order, restore data."""
    from pathlib import Path

    from app.models.nodes import View
    from app.services.app_home_override import validate_home_composition
    from app.services.app_lifecycle import install_app
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )
    from app.services.app_service import create_app_for_user
    from app.services.application_definitions import compile_application_definition
    from app.services.change_event import emit_change_event
    from app.services.dashboard_service import create_dashboard
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_import_reservations import (
        reserved_import_entry,
    )
    from app.services.entry_write_scope import entry_write_scope
    from app.services.portable_package import resolve_admitted_package
    from app.services.track_service import create_track_in_space
    from app.services.workspace_storage_usage import check_quota

    if not archive.packages or not archive.apps:
        raise BadRequestError(message="Packaged archive is invalid")
    # Bundles materialized from the archive for this attempt. Successful
    # admission is durable host state (retries reuse it); App/graph cleanup
    # does not remove shared package trees.
    created_bundle_dirs: list[Path] = []
    libraries: dict[str, Any] = {}
    for payload in archive.packages:
        libraries[payload.slug] = await resolve_admitted_package(
            payload,
            files,
            user_id=user_id,
            workspace_id=workspace_id,
            created_bundle_dirs=created_bundle_dirs,
        )
    ordered_apps = _order_archive_apps(
        [
            (
                spec.model_copy(
                    update={"manifest": libraries[spec.package_slug].manifest}
                )
                if spec.package_slug
                else spec
            )
            for spec in archive.apps
        ]
    )
    roots: list[Any] = []
    stored_keys: list[str] = []
    mapping: dict[str, str] = {}
    app_names: dict[str, str] = {}
    events: list[dict] = []
    atomic = graph_transaction_available()
    outboxes = []
    try:
        async with entry_write_scope(f"portable-package:{workspace_id}"):
            from app.services.portable_preflight import preflight_packaged_apps

            await preflight_packaged_apps(
                ordered_apps, libraries, workspace_id=workspace_id
            )
            total_file_bytes = sum(
                len(files[a.member])
                for t in _tracks(archive)
                for e in t.entries
                for a in e.attachments
                if a.source_type == "file"
            )
            if await check_quota(workspace_id, total_file_bytes) is not None:
                raise BadRequestError(
                    message="This import exceeds the workspace's attachment storage quota"
                )
            from app.services.app_batch_install import (
                default_settings_for_library_model,
            )

            track_pairs: list[tuple[PortableTrack, Track]] = []
            app_pairs: list[tuple[PortableApp, App]] = []
            for spec in ordered_apps:
                if spec.package_slug:
                    library = libraries.get(spec.package_slug)
                    if library is None:
                        raise BadRequestError(
                            message=f"Archive is missing package {spec.package_slug!r}"
                        )
                    settings = dict(spec.settings or {})
                    if not settings:
                        settings = await default_settings_for_library_model(library.id)
                    installed = await install_app(
                        workspace_id=workspace_id,
                        library_cp_id=library.id,
                        actor_id=user_id,
                        name_override=spec.name,
                        description_override=spec.description,
                        icon_override=spec.icon or None,
                        settings=settings,
                        include_seed_data=False,
                        visibility="private",
                        _change_event_sink=events.append,
                        _require_fresh=True,
                        _created_app_sink=roots.append,
                    )
                    if installed.get("status") == "awaiting_settings":
                        raise BadRequestError(
                            message=(
                                "A packaged App still requires settings after defaults "
                                "were applied. Install it from the library, complete "
                                "settings, then retry import."
                            )
                        )
                    if installed.get("status") != "active" or not installed.get(
                        "app_id"
                    ):
                        raise BadRequestError(
                            message="Packaged App install did not complete"
                        )
                    app = await App.get(installed["app_id"])
                    if app is None:
                        raise BadRequestError(message="Installed App is unavailable")
                    if spec.icon:
                        app.icon = spec.icon
                    if spec.accent_color:
                        app.accent_color = spec.accent_color
                    await app.save()
                    cp = await get_app_attached_operational_model(app)
                    if cp is None:
                        raise BadRequestError(
                            message="Installed App configuration is unavailable"
                        )
                    if spec.model_ref:
                        mapping[spec.model_ref] = cp.id
                    existing_tags = await _bounded(cp, CONTAINS, "Tag")
                    for item in spec.tags:
                        tag = next(
                            (
                                t
                                for t in existing_tags
                                if compute_fold(t.name) == compute_fold(item.name)
                            ),
                            None,
                        )
                        if tag is None:
                            tag = await Tag.create(
                                name=item.name,
                                name_fold=compute_fold(item.name),
                                app_id=app.id,
                            )
                            await cp.connect(tag, edge=CONTAINS)
                        tag.color = item.color
                        tag.group_key = item.group_key
                        tag.aliases = item.aliases
                        tag.applies_to_entry_types = item.applies_to_entry_types
                        await tag.save()
                        mapping[item.ref] = tag.id
                    for item in spec.tags:
                        tag = await Tag.get(mapping[item.ref])
                        tag.parent_tag_id = (
                            mapping[item.parent_ref] if item.parent_ref else None
                        )
                        await tag.save()
                    mapping[spec.ref] = app.id
                    app_names[spec.ref] = app.name
                    app_pairs.append((spec, app))
                    track_pairs.extend(
                        await _overlay_installed_packaged_tracks(
                            user_id=user_id,
                            workspace_id=workspace_id,
                            app=app,
                            spec=spec,
                            mapping=mapping,
                            events=events,
                        )
                    )
                else:
                    name = await _available_name(App, "name", spec.name, workspace_id)
                    app = await create_app_for_user(
                        user_id,
                        name,
                        description=spec.description,
                        icon=spec.icon,
                        accent_color=spec.accent_color,
                        workspace_id=workspace_id,
                        visibility="private",
                        change_event_sink=events.append,
                    )
                    roots.append(app)
                    mapping[spec.ref] = app.id
                    app_names[spec.ref] = app.name
                    app_pairs.append((spec, app))
                    cp = await get_app_attached_operational_model(app)
                    if spec.model_ref and cp is not None:
                        mapping[spec.model_ref] = cp.id
                    for item in spec.tags:
                        tag = await Tag.create(
                            name=item.name,
                            name_fold=compute_fold(item.name),
                            color=item.color,
                            app_id=app.id,
                            group_key=item.group_key,
                            aliases=item.aliases,
                            applies_to_entry_types=item.applies_to_entry_types,
                        )
                        if cp is not None:
                            await cp.connect(tag, edge=CONTAINS)
                        mapping[item.ref] = tag.id
                    for item in spec.tags:
                        if item.parent_ref:
                            tag = await Tag.get(mapping[item.ref])
                            tag.parent_tag_id = mapping[item.parent_ref]
                            await tag.save()
                    for track_spec in spec.tracks:
                        track = await create_track_in_space(
                            user_id,
                            title=track_spec.title,
                            purpose=track_spec.purpose,
                            icon=track_spec.icon,
                            accent_color=track_spec.accent_color,
                            workspace_id=workspace_id,
                            app_id=app.id,
                            visibility="private",
                            change_event_sink=events.append,
                        )
                        await _restore_config(track, track_spec, mapping)
                        track_pairs.append((track_spec, track))
            for track_spec, track in track_pairs:
                await _authorize(user_id, "track", track.id, "read")
                for item in track_spec.entries:
                    decision = await evaluate(
                        subject=Subject(kind="human", id=user_id),
                        action="entry.create",
                        resource=Resource(
                            kind="track", id=track.id, scope=f"track:{track.id}"
                        ),
                    )
                    if not decision.allowed:
                        raise InsufficientPermissionsError(
                            message="You cannot import records into this Track"
                        )
                    if item.type_ref not in mapping:
                        raise BadRequestError(
                            message="An imported record type is unavailable on the installed App"
                        )
                    entry = await Entry.create(
                        track_id=track.id,
                        type_id=mapping[item.type_ref],
                        title=item.title,
                        author_id=user_id,
                        status="importing",
                    )
                    await track.connect(entry, edge=CONTAINS)
                    mapping[item.ref] = entry.id
                    for attachment in item.attachments:
                        new_file = await _restore_file(
                            entry, attachment, files, user_id, stored_keys
                        )
                        mapping[attachment.ref] = new_file.id
            for track_spec, track in track_pairs:
                cp = await get_track_attached_operational_model(track)
                if cp is not None:
                    cp.manifest = _remap(cp.manifest, mapping, app_names=app_names)
                    await cp.save()
                for item in track_spec.entry_types:
                    if item.ref in mapping:
                        et = await EntryType.get(mapping[item.ref])
                        et.form_schema = _remap(
                            item.form_schema, mapping, app_names=app_names
                        )
                        await et.save()
                for item in track_spec.views:
                    if item.ref in mapping:
                        view = await View.get(mapping[item.ref])
                        view.config = _remap(item.config, mapping)
                        await view.save()
            for spec, app in app_pairs:
                if spec.package_slug:
                    continue
                cp = await get_app_attached_operational_model(app)
                if cp is None:
                    continue
                cp.manifest = _remap(
                    _data_manifest(spec.manifest), mapping, app_names=app_names
                )
                app.settings_schema = dict(
                    (cp.manifest.get("app") or {}).get("settings_schema") or {}
                )
                await app.save()
                await cp.save()
                await compile_application_definition(
                    app_node=app,
                    manifest=cp.manifest,
                    source_operational_model_id=cp.id,
                    source_kind="local",
                )
            skip_refs = _collect_incomplete_import_refs(track_pairs, mapping)
            skipped_entries: list[str] = []
            for track_spec, track in track_pairs:
                for item in track_spec.entries:
                    if item.ref in skip_refs:
                        skipped_entries.append(_record_label(item))
                        await _drop_reserved_import_entry(
                            item=item, mapping=mapping, stored_keys=stored_keys
                        )
                        continue
                    entry = await Entry.get(mapping[item.ref])
                    et = next(
                        t for t in track_spec.entry_types if t.ref == item.type_ref
                    )
                    attachment_ids = [
                        mapping[a.ref] for a in item.attachments if a.ref in mapping
                    ]
                    with reserved_import_entry(entry, user_id):
                        restored = await create_entry_in_track(
                            track=track,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            title=item.title,
                            body=_body_links(item.body, mapping),
                            type_id=mapping[item.type_ref],
                            custom_fields=_field_values(
                                item, et, mapping, track_spec.manifest
                            ),
                            tags=[mapping[t] for t in item.tags if t in mapping],
                            attachment_ids=attachment_ids,
                            change_event_sink=events.append,
                        )
                        from app.services.app_invariant_guards import (
                            enforce_protected_field_write,
                        )
                        from app.services.operational_model_compile import (
                            slug_manifest_key,
                        )

                        await enforce_protected_field_write(
                            workspace_id=workspace_id,
                            entry_type_key=slug_manifest_key(
                                str(
                                    et.form_schema.get("_manifest_entry_type_key")
                                    or et.name
                                )
                            ),
                            proposed_custom_fields={},
                            proposed_fields={"status": item.status},
                            track_id=track.id,
                            entry_id=restored.id,
                            principal_id=user_id,
                        )
                        restored.status = item.status
                        if item.created_at is not None:
                            restored.created_at = item.created_at
                        if item.updated_at is not None:
                            restored.updated_at = item.updated_at
                        await restored.save()
                        from app.services.retrieval.indexing import enqueue_entry

                        await enqueue_entry(restored)
            for spec, app in app_pairs:
                if spec.home_composition is not None:
                    app.home_composition = await validate_home_composition(
                        user_id=user_id,
                        app_id=app.id,
                        home=_remap(spec.home_composition, mapping),
                    )
                    app.home_revision = 1
                    await app.save()
                for dashboard in spec.dashboards:
                    await create_dashboard(
                        user_id=user_id,
                        app_id=app.id,
                        name=dashboard.name,
                        layout=dashboard.layout,
                        widgets=_remap(dashboard.widgets, mapping),
                        is_default=dashboard.is_default,
                    )
            from app.models.nodes import App as AppNode
            from app.models.nodes import Track as TrackNode
            from app.services.resource_events import queue_resource_event

            kinds = {"App": AppNode, "Track": TrackNode, "Entry": Entry}
            for event in events:
                model = kinds.get(event["resource_type"])
                if model:
                    event["after"] = await (
                        await model.get(event["resource_id"])
                    ).export(flat=True)
                outbox = await queue_resource_event(
                    event, workspace_id, atomic, namespace="portable-import-v1"
                )
                if outbox:
                    outboxes.append(outbox)
            retained_file_bytes = sum(
                len(files[a.member])
                for t in _tracks(archive)
                for e in t.entries
                if e.ref in mapping
                for a in e.attachments
                if a.source_type == "file" and a.ref in mapping
            )
            from app.services.portable_preflight import charge_import_storage

            await charge_import_storage(workspace_id, retained_file_bytes)
    except BaseException:
        await _cleanup_import_attempt(roots, stored_keys, workspace_id, atomic)
        raise
    if atomic:
        from app.services.workspace_membership import _deliver

        for outbox in outboxes:
            await _deliver(outbox)
    else:
        for event in events:
            await emit_change_event(**event)
    from app.services.attachment_metadata.service import get_metadata_extraction_service

    for track_spec in _tracks(archive):
        for entry in track_spec.entries:
            if entry.ref not in mapping:
                continue
            for attachment in entry.attachments:
                if attachment.source_type == "file" and attachment.ref in mapping:
                    try:
                        get_metadata_extraction_service().schedule_extraction(
                            mapping[attachment.ref]
                        )
                    except Exception:
                        logger.exception(
                            "Could not schedule imported attachment metadata"
                        )
    return {
        **archive_summary(archive),
        "entries": sum(e.ref in mapping for t in _tracks(archive) for e in t.entries),
        "attachments": sum(
            a.ref in mapping
            for t in _tracks(archive)
            for e in t.entries
            for a in e.attachments
        ),
        "resource_ids": [root.id for root in roots],
        "skipped_entries": skipped_entries,
        "skipped_entry_count": len(skipped_entries),
    }


async def import_resources(
    *, user_id: str, workspace_id: str, data: bytes, app_id: str = ""
) -> dict:
    """Restore private copies. PG is atomic; local stores compensate owned nodes/files."""
    from app.models.nodes import View
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )
    from app.services.app_service import create_app_for_user
    from app.services.application_definitions import compile_application_definition
    from app.services.change_event import emit_change_event
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_import_reservations import (
        reserved_import_entry,
    )
    from app.services.entry_write_scope import entry_write_scope
    from app.services.permissions import (
        can_create_app_under_workspace,
        can_create_track_under_workspace,
    )
    from app.services.track_service import create_track_in_space

    archive, files = await read_archive(data)
    if (
        archive.apps and not await can_create_app_under_workspace(user_id, workspace_id)
    ) or (
        _tracks(archive)
        and not await can_create_track_under_workspace(user_id, workspace_id)
    ):
        raise InsufficientPermissionsError(
            message="You cannot create the selected resources in this workspace"
        )
    parent = await App.get(app_id) if app_id else None
    if app_id:
        if (
            archive.kind != "track"
            or parent is None
            or parent.workspace_id != workspace_id
        ):
            raise BadRequestError(message="Track import destination is invalid")
        await _authorize(user_id, "app", app_id, "update")
    if archive.packages:
        if app_id:
            raise BadRequestError(
                message="Packaged App archives must be imported from Manage Apps"
            )
        return await _import_packaged_archive(
            user_id=user_id,
            workspace_id=workspace_id,
            archive=archive,
            files=files,
        )
    roots: list[Any] = []
    stored_keys: list[str] = []
    mapping: dict[str, str] = {}
    app_names: dict[str, str] = {}
    events: list[dict] = []
    atomic = graph_transaction_available()
    outboxes = []
    try:
        async with entry_write_scope(f"portable:{workspace_id}"):
            from app.services.workspace_storage_usage import check_quota

            total_file_bytes = sum(
                len(files[a.member])
                for t in _tracks(archive)
                for e in t.entries
                for a in e.attachments
                if a.source_type == "file"
            )
            if await check_quota(workspace_id, total_file_bytes) is not None:
                raise BadRequestError(
                    message="This import exceeds the workspace's attachment storage quota"
                )
            track_pairs = []
            app_pairs = []
            for spec in archive.apps:
                name = await _available_name(App, "name", spec.name, workspace_id)
                app = await create_app_for_user(
                    user_id,
                    name,
                    description=spec.description,
                    icon=spec.icon,
                    accent_color=spec.accent_color,
                    workspace_id=workspace_id,
                    visibility="private",
                    change_event_sink=events.append,
                )
                roots.append(app)
                mapping[spec.ref] = app.id
                app_names[spec.ref] = app.name
                app_pairs.append((spec, app))
                for track_spec in spec.tracks:
                    track = await create_track_in_space(
                        user_id,
                        title=track_spec.title,
                        purpose=track_spec.purpose,
                        icon=track_spec.icon,
                        accent_color=track_spec.accent_color,
                        workspace_id=workspace_id,
                        app_id=app.id,
                        visibility="private",
                        change_event_sink=events.append,
                    )
                    track_pairs.append((track_spec, track))
            for spec in archive.tracks:
                title = await _available_name(
                    Track, "title", spec.title, workspace_id, parent
                )
                track = await create_track_in_space(
                    user_id,
                    title=title,
                    purpose=spec.purpose,
                    icon=spec.icon,
                    accent_color=spec.accent_color,
                    workspace_id=workspace_id,
                    app_id=app_id or None,
                    visibility="private",
                    change_event_sink=events.append,
                )
                roots.append(track)
                track_pairs.append((spec, track))
            for spec, app in app_pairs:
                cp = await get_app_attached_operational_model(app)
                if spec.model_ref:
                    mapping[spec.model_ref] = cp.id
                for item in spec.tags:
                    tag = await Tag.create(
                        name=item.name,
                        name_fold=compute_fold(item.name),
                        color=item.color,
                        app_id=app.id,
                        group_key=item.group_key,
                        aliases=item.aliases,
                        applies_to_entry_types=item.applies_to_entry_types,
                    )
                    await cp.connect(tag, edge=CONTAINS)
                    mapping[item.ref] = tag.id
                for item in spec.tags:
                    if item.parent_ref:
                        tag = await Tag.get(mapping[item.ref])
                        tag.parent_tag_id = mapping[item.parent_ref]
                        await tag.save()
            for spec, track in track_pairs:
                await _restore_config(track, spec, mapping)
            # Allocate every record before canonical creation: forward and cyclic
            # relations resolve using normal scope/type/permission checks.
            for spec, track in track_pairs:
                await _authorize(user_id, "track", track.id, "read")
                for item in spec.entries:
                    decision = await evaluate(
                        subject=Subject(kind="human", id=user_id),
                        action="entry.create",
                        resource=Resource(
                            kind="track", id=track.id, scope=f"track:{track.id}"
                        ),
                    )
                    if not decision.allowed:
                        raise InsufficientPermissionsError(
                            message="You cannot import records into this Track"
                        )
                    entry = await Entry.create(
                        track_id=track.id,
                        type_id=mapping[item.type_ref],
                        title=item.title,
                        author_id=user_id,
                        status="importing",
                    )
                    await track.connect(entry, edge=CONTAINS)
                    mapping[item.ref] = entry.id
                    for attachment in item.attachments:
                        new_file = await _restore_file(
                            entry, attachment, files, user_id, stored_keys
                        )
                        mapping[attachment.ref] = new_file.id
            # All configuration bindings now have destination identities.
            for spec, track in track_pairs:
                cp = await get_track_attached_operational_model(track)
                cp.manifest = _remap(cp.manifest, mapping, app_names=app_names)
                await cp.save()
                for item in spec.entry_types:
                    et = await EntryType.get(mapping[item.ref])
                    et.form_schema = _remap(
                        item.form_schema, mapping, app_names=app_names
                    )
                    await et.save()
                for item in spec.views:
                    view = await View.get(mapping[item.ref])
                    view.config = _remap(item.config, mapping)
                    await view.save()
            for spec, app in app_pairs:
                cp = await get_app_attached_operational_model(app)
                cp.manifest = _remap(
                    _data_manifest(spec.manifest), mapping, app_names=app_names
                )
                app.settings_schema = dict(
                    (cp.manifest.get("app") or {}).get("settings_schema") or {}
                )
                await app.save()
                await cp.save()
                await compile_application_definition(
                    app_node=app,
                    manifest=cp.manifest,
                    source_operational_model_id=cp.id,
                    source_kind="local",
                )
            skip_refs = _collect_incomplete_import_refs(track_pairs, mapping)
            skipped_entries = []
            for spec, track in track_pairs:
                for item in spec.entries:
                    if item.ref in skip_refs:
                        skipped_entries.append(_record_label(item))
                        await _drop_reserved_import_entry(
                            item=item, mapping=mapping, stored_keys=stored_keys
                        )
                        continue
                    entry = await Entry.get(mapping[item.ref])
                    et = next(t for t in spec.entry_types if t.ref == item.type_ref)
                    attachment_ids = [
                        mapping[a.ref] for a in item.attachments if a.ref in mapping
                    ]
                    with reserved_import_entry(entry, user_id):
                        restored = await create_entry_in_track(
                            track=track,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            title=item.title,
                            body=_body_links(item.body, mapping),
                            type_id=mapping[item.type_ref],
                            custom_fields=_field_values(
                                item, et, mapping, spec.manifest
                            ),
                            tags=[mapping[t] for t in item.tags if t in mapping],
                            attachment_ids=attachment_ids,
                            change_event_sink=events.append,
                        )
                        from app.services.app_invariant_guards import (
                            enforce_protected_field_write,
                        )
                        from app.services.operational_model_compile import (
                            slug_manifest_key,
                        )

                        await enforce_protected_field_write(
                            workspace_id=workspace_id,
                            entry_type_key=slug_manifest_key(
                                str(
                                    et.form_schema.get("_manifest_entry_type_key")
                                    or et.name
                                )
                            ),
                            proposed_custom_fields={},
                            proposed_fields={"status": item.status},
                            track_id=track.id,
                            entry_id=restored.id,
                            principal_id=user_id,
                        )
                        restored.status = item.status
                        if item.created_at is not None:
                            restored.created_at = item.created_at
                        if item.updated_at is not None:
                            restored.updated_at = item.updated_at
                        await restored.save()
                        from app.services.retrieval.indexing import enqueue_entry

                        await enqueue_entry(restored)
            for spec, app in app_pairs:
                from app.services.app_home_override import validate_home_composition
                from app.services.dashboard_service import create_dashboard

                if spec.home_composition is not None:
                    app.home_composition = await validate_home_composition(
                        user_id=user_id,
                        app_id=app.id,
                        home=_remap(spec.home_composition, mapping),
                    )
                    app.home_revision = 1
                    await app.save()
                for dashboard in spec.dashboards:
                    await create_dashboard(
                        user_id=user_id,
                        app_id=app.id,
                        name=dashboard.name,
                        layout=dashboard.layout,
                        widgets=_remap(dashboard.widgets, mapping),
                        is_default=dashboard.is_default,
                    )
            # Refresh creation snapshots after all schema, status and Home work;
            # PG outboxes share the same commit as the newly restored graph.
            from app.models.nodes import App as AppNode
            from app.models.nodes import Track as TrackNode
            from app.services.resource_events import queue_resource_event

            kinds = {"App": AppNode, "Track": TrackNode, "Entry": Entry}
            for event in events:
                model = kinds.get(event["resource_type"])
                if model:
                    event["after"] = await (
                        await model.get(event["resource_id"])
                    ).export(flat=True)
                outbox = await queue_resource_event(
                    event, workspace_id, atomic, namespace="portable-import-v1"
                )
                if outbox:
                    outboxes.append(outbox)
            retained_file_bytes = sum(
                len(files[a.member])
                for t in _tracks(archive)
                for e in t.entries
                if e.ref in mapping
                for a in e.attachments
                if a.source_type == "file" and a.ref in mapping
            )
            from app.services.portable_preflight import charge_import_storage

            await charge_import_storage(workspace_id, retained_file_bytes)
    except BaseException:
        await _cleanup_import_attempt(roots, stored_keys, workspace_id, atomic)
        raise
    if atomic:
        from app.services.workspace_membership import _deliver

        for outbox in outboxes:
            await _deliver(outbox)
    else:
        for event in events:
            await emit_change_event(**event)
    from app.services.attachment_metadata.service import get_metadata_extraction_service

    for spec in _tracks(archive):
        for entry in spec.entries:
            if entry.ref not in mapping:
                continue
            for attachment in entry.attachments:
                if attachment.source_type == "file" and attachment.ref in mapping:
                    try:
                        get_metadata_extraction_service().schedule_extraction(
                            mapping[attachment.ref]
                        )
                    except Exception:
                        # The graph/file commit succeeded. Keep pending metadata
                        # retryable instead of claiming the import itself failed.
                        logger.exception(
                            "Could not schedule imported attachment metadata"
                        )
    return {
        **archive_summary(archive),
        "entries": sum(e.ref in mapping for t in _tracks(archive) for e in t.entries),
        "attachments": sum(
            a.ref in mapping
            for t in _tracks(archive)
            for e in t.entries
            for a in e.attachments
        ),
        "resource_ids": [root.id for root in roots],
        "skipped_entries": skipped_entries,
        "skipped_entry_count": len(skipped_entries),
    }
