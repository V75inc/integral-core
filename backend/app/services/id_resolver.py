"""Schema-agnostic resolution of raw node ids → human-readable labels.

Integral is schema-flexible: ids look like ``n.<Type>.<hex>`` (nodes) and
``o.<Type>.<hex>`` (objects, e.g. ``o.User``). Any HUMAN-FACING surface that
might echo a raw id — the agent's chat prose, staging approval cards — runs
``humanize_ids`` so the reader sees a name, not an opaque id.

Design goals (per the substrate usability review):

- **Seamless & adaptable:** one generic pass, not per-call-site edits. New node
  types and new stagers are covered for free — labels come from a dynamic
  priority chain (``title`` → ``name`` → …), and nodes load via jvspatial's
  discriminator-dispatching ``Node.get`` regardless of class.
- **Cheap:** all ids in a blob resolve in ONE batched ``$in`` query per
  collection, backed by jvspatial's in-memory LRU cache.
- **Safe:** best-effort. Any resolution failure leaves the original text
  untouched; an unresolved id degrades to a short ``<Type> …abcd`` form, never
  a stack trace. We humanize human-facing TEXT only — never tool-call inputs or
  the raw ``diff_machine`` inspector, which legitimately need ids.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)

# ``n.<PascalType>.<24-hex>`` or ``o.<PascalType>.<24-hex>``. Word-boundary
# anchored so trailing punctuation / JSON quotes aren't swallowed. ``{{…}}``
# template tokens never match (lowercase dotted path, no PascalCase type), and
# singleton ids like ``n.IntegralApp.integral`` (non-hex tail) are intentionally
# skipped — they're rare in prose and have stable human names of their own.
_ID_RE = re.compile(r"\b([no]\.[A-Z][A-Za-z0-9]*\.[0-9a-f]{24})\b")

# Markdown link targets and in-app routes keep raw ids so persisted assistant
# links survive reload (see notification_paths / resourcePaths contract).
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_INTERNAL_ROUTE_RE = re.compile(
    r"/(?:tracks|apps|workspaces|content-profiles)/"
    r"[no]\.[A-Z][A-Za-z0-9]*\.[0-9a-f]{24}"
    r"(?:\?[^)\s\]>]*)?"
)
_ENTRY_QUERY_RE = re.compile(r"([?&]entry=)([no]\.[A-Z][A-Za-z0-9]*\.[0-9a-f]{24})")
_PLACEHOLDER_RE = re.compile(r"\x00H(\d+)\x00")

# Display-field priority. Covers every node type with one ordered sweep:
# Track/Entry/ChatThread use ``title``; most else ``name``; User
# ``display_name``; log-ish records fall back to a text/content snippet.
_LABEL_FIELDS = ("title", "name", "display_name", "filename", "content", "text")

_SNIPPET_MAX = 80


def _short(node_id: str) -> str:
    """Last-resort label for an unresolvable id: ``<Type> …<last4>``."""
    parts = node_id.split(".")
    if len(parts) >= 3 and parts[2]:
        return f"{parts[1]} …{parts[2][-4:]}"
    return node_id


def _pick_label(node, node_id: str) -> str:
    cls = type(node).__name__
    if cls in ("User", "AuthUser"):
        for field in ("display_name", "email", "user_id"):
            val = getattr(node, field, None)
            if val:
                return str(val)
    for field in _LABEL_FIELDS:
        val = getattr(node, field, None)
        if val:
            text = str(val).strip()
            if text:
                return (
                    text
                    if len(text) <= _SNIPPET_MAX
                    else text[: _SNIPPET_MAX - 1] + "…"
                )
    return _short(node_id)


async def _content_profile_label(cp) -> str:
    """Name a ContentProfile by the resource it shapes, not its own field.

    A profile's own ``name`` is usually a generic "Default", which tells a user
    nothing. The useful identity is the Track/App it's attached to — "the
    Opportunities profile" reads far better than "Default" or a raw id. A draft
    attaches to no resource, so resolve via its published parent (``draft_of_id``).
    Library packages keep their own (meaningful) package name.
    """
    if getattr(cp, "library_package", False):
        return str(getattr(cp, "name", None) or _short(cp.id))

    candidate_ids = [cp.id]
    parent = getattr(cp, "draft_of_id", None)
    if parent:
        candidate_ids.append(parent)

    try:
        from app.models.nodes import App, Track

        for cid in candidate_ids:
            tracks = await Track.find({"context.attached_content_profile_id": cid})
            for track in tracks:
                title = (getattr(track, "title", "") or "").strip()
                if title:
                    return f"the {title} profile"
            apps = await App.find({"context.attached_content_profile_id": cid})
            for app in apps:
                name = (getattr(app, "name", "") or "").strip()
                if name:
                    return f"the {name} profile"
    except Exception:  # noqa: BLE001
        logger.debug("id_resolver: content-profile owner lookup failed", exc_info=True)

    return _pick_label(cp, cp.id)


async def resolve_id_labels(ids: Iterable[str]) -> Dict[str, str]:
    """Batch-resolve node ids → labels. One ``$in`` query per collection.

    Always returns a label for every input id (short fallback for misses).
    """
    uniq = {i for i in ids if i}
    if not uniq:
        return {}

    # Ensure concrete Node/Object subclasses are imported so jvspatial's
    # discriminator dispatch (find_subclass_by_name) can resolve each row to
    # its real class. The server imports these at boot; this makes the resolver
    # robust when called from a lean context too.
    try:
        import app.models.nodes  # noqa: F401
    except Exception:  # noqa: BLE001
        logger.debug("id_resolver: app.models.nodes import failed", exc_info=True)

    out: Dict[str, str] = {}

    # Users resolve through the canonical principal resolver, which handles BOTH
    # the graph-node form (n.User.*) AND the AuthUser principal form (o.User.*) —
    # the latter is NOT a queryable graph node, so the generic node/object batch
    # below would miss it and degrade to "User …abcd".
    user_ids = [i for i in uniq if ".User." in i]
    if user_ids:
        try:
            from app.services.permissions import (
                batch_resolve_users_by_principal_ids,
            )

            umap = await batch_resolve_users_by_principal_ids(user_ids)
            for pid, user in umap.items():
                if user is not None:
                    out[pid] = _pick_label(user, pid)
        except Exception:  # noqa: BLE001
            logger.debug("id_resolver: user batch resolve failed", exc_info=True)

    node_ids = [i for i in uniq if i.startswith("n.") and i not in out]
    obj_ids = [i for i in uniq if i.startswith("o.") and i not in out]

    if node_ids:
        try:
            from jvspatial.core import Node

            for node in await Node.find({"id": {"$in": node_ids}}):
                if type(node).__name__ == "ContentProfile":
                    # Profiles are most usefully named by what they shape.
                    out[node.id] = await _content_profile_label(node)
                else:
                    out[node.id] = _pick_label(node, node.id)
        except Exception:  # noqa: BLE001
            logger.debug("id_resolver: node batch resolve failed", exc_info=True)

    if obj_ids:
        try:
            from jvspatial.core import Object

            for obj in await Object.find({"id": {"$in": obj_ids}}):
                out[obj.id] = _pick_label(obj, obj.id)
        except Exception:  # noqa: BLE001
            logger.debug("id_resolver: object batch resolve failed", exc_info=True)

    for i in uniq:
        out.setdefault(i, _short(i))
    return out


def find_ids(text: str) -> List[str]:
    """Return all raw node ids present in ``text`` (deduped, order-preserving)."""
    if not text or "." not in text:
        return []
    seen: Dict[str, None] = {}
    for match in _ID_RE.findall(text):
        seen.setdefault(match, None)
    return list(seen.keys())


def _mask_humanize_protected(text: str) -> Tuple[str, List[str]]:
    """Stash markdown link targets and internal routes before id humanization."""
    slots: List[str] = []

    def stash(fragment: str) -> str:
        slots.append(fragment)
        return f"\x00H{len(slots) - 1}\x00"

    def mask_md_link(match: re.Match[str]) -> str:
        return f"[{match.group(1)}]({stash(match.group(2))})"

    text = _MD_LINK_RE.sub(mask_md_link, text)
    text = _INTERNAL_ROUTE_RE.sub(lambda m: stash(m.group(0)), text)
    text = _ENTRY_QUERY_RE.sub(lambda m: f"{m.group(1)}{stash(m.group(2))}", text)
    return text, slots


def _unmask_humanize_protected(text: str, slots: List[str]) -> str:
    if not slots:
        return text

    def repl(match: re.Match[str]) -> str:
        return slots[int(match.group(1))]

    return _PLACEHOLDER_RE.sub(repl, text)


async def humanize_ids(text: str) -> str:
    """Replace every raw node id in ``text`` with its human label.

    Idempotent (a label has no id shape) and best-effort — returns ``text``
    unchanged on any failure. Leaves ``{{…}}`` placeholders untouched.

    Markdown link targets and Integral frontend route paths are preserved so
    assistant citations stay navigable after persistence.
    """
    masked, slots = _mask_humanize_protected(text)
    ids = find_ids(masked)
    if not ids:
        return text
    try:
        labels = await resolve_id_labels(ids)
    except Exception:  # noqa: BLE001
        logger.debug("humanize_ids: resolve failed", exc_info=True)
        return text

    humanized = _ID_RE.sub(lambda m: labels.get(m.group(1), m.group(1)), masked)
    return _unmask_humanize_protected(humanized, slots)
