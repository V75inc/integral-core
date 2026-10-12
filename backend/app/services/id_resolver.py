"""Schema-agnostic resolution of raw node ids → human-readable labels.

Integral is schema-flexible: ids look like ``n.<Type>.<hex>`` (nodes) and
``o.<Type>.<hex>`` (objects, e.g. ``o.User``). Any HUMAN-FACING surface that
might echo a raw id — the agent's chat prose, staging approval cards — runs
``humanize_ids`` so the reader sees a name, not an opaque id.

Design goals (per the substrate usability review):

- **Seamless & adaptable:** one generic pass, not per-call-site edits. New node
  types need a governed read contract before label disclosure; labels use a
  priority chain (``title`` → ``name`` → …) after the corresponding Core
  read contract admits the exact target.
- **Current:** labels require explicit recipient identity, canonical scope
  and current resource read admission; unknown families remain opaque.
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
    r"/(?:tracks|apps|workspaces|operational-models)/"
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


async def resolve_id_labels(
    ids: Iterable[str], *, user_id: str | None = None, workspace_id: str | None = None
) -> Dict[str, str]:
    """Resolve only current readable metadata for an explicit recipient.

    Missing identity and unsupported graph/object families use an opaque
    fallback. An arbitrary ID in generated prose is never read authority.
    """
    uniq = {identity for identity in ids if identity}
    out = {identity: _short(identity) for identity in uniq}
    if not uniq or not user_id:
        return out
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.nodes import App, Entry, OperationalModel, Track, View, Workspace
    from app.schemas.policy import Resource
    from app.services.model_authority import (
        model_action_authorized,
        model_scope_matches,
    )
    from app.services.permissions import can_view_view, get_user_node, resolve_role
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    kinds = {
        "App": ("app", App),
        "WorkspaceApp": ("app", App),
        "Track": ("track", Track),
        "Entry": ("entry", Entry),
        "View": ("view", View),
        "Workspace": ("workspace", Workspace),
        "OperationalModel": ("operational_model", OperationalModel),
    }
    with isolated_permissions_cache():
        principal = await get_user_node(user_id)
        if principal is None:
            return out
        for identity in uniq:
            try:
                if identity in {principal.id, principal.user_id}:
                    out[identity] = _pick_label(principal, identity)
                    continue
                parts = identity.split(".")
                if len(parts) != 3 or parts[0] != "n" or parts[1] not in kinds:
                    continue
                kind, node_type = kinds[parts[1]]
                if kind == "operational_model":
                    resource = Resource(
                        kind=kind, id=identity, scope=f"{kind}:{identity}"
                    )
                    if workspace_id and not await model_scope_matches(
                        workspace_id, resource, principal_id=user_id
                    ):
                        continue
                    if not await model_action_authorized(
                        user_id, "operational_model.read", resource
                    ):
                        continue
                else:
                    location = await locate_resource(
                        kind, identity, allowed_app_states=DATA_APP_STATES
                    )
                    if location is None or (
                        workspace_id and location.workspace_id != workspace_id
                    ):
                        continue
                    allowed = (
                        await can_view_view(user_id, identity)
                        if kind == "view"
                        else await resolve_role(user_id, kind, identity) is not None
                    )
                    if not allowed:
                        continue
                node = await node_type.find_one({"id": identity})
                if node is not None:
                    # Model labels use the admitted model's own metadata;
                    # naming an arbitrary attached parent would need its own gate.
                    out[identity] = _pick_label(node, identity)
            except Exception:  # noqa: BLE001 — best-effort disclosure fails closed
                logger.debug("id_resolver: label admission failed", exc_info=True)
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


async def humanize_ids(
    text: str, *, user_id: str | None = None, workspace_id: str | None = None
) -> str:
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
        labels = await resolve_id_labels(
            ids, user_id=user_id, workspace_id=workspace_id
        )
    except Exception:  # noqa: BLE001
        logger.debug("humanize_ids: resolve failed", exc_info=True)
        return text

    humanized = _ID_RE.sub(lambda m: labels.get(m.group(1), m.group(1)), masked)
    return _unmask_humanize_protected(humanized, slots)
