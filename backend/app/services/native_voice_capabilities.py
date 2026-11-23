"""Server-owned, bounded catalogue offers for authenticated private voice reads."""

import asyncio
import re
import secrets

from app.agentive.tooling.manifest import ToolSpec

_MAX_OFFERS = 4
_MAX_LABELS = 20


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


async def discover_voice_reads(query: str) -> dict[str, ToolSpec]:
    """Search reviewed catalogue descriptions without inference or remote calls."""
    from app.agentive.tooling.catalogue import build_voice_quick_read_catalogue

    words = _words(query)
    rows = await asyncio.to_thread(build_voice_quick_read_catalogue)
    weighted = []
    for spec in rows.values():
        meta = spec.voice_quick_read
        if meta is not None:
            score = 3 * len(words & _words(meta.title)) + len(
                words & _words(meta.terms)
            )
            if score:
                weighted.append((score, spec))
    weighted.sort(key=lambda item: (-item[0], item[1].name))
    return {secrets.token_hex(16): spec for _, spec in weighted[:_MAX_OFFERS]}


async def invoke_voice_read(
    spec: ToolSpec, *, principal: str, workspace: str
) -> str | None:
    """Call the live authorized dispatcher and return only a narrow label view."""
    from app.agentive.tooling.dispatch import dispatch_tool

    meta = spec.voice_quick_read
    if meta is None:
        return None
    response = await dispatch_tool(
        spec.name, {}, principal_id=principal, scope=workspace, voice_read_offer=spec
    )
    if response.is_error or not isinstance(response.data, dict):
        return None
    records = response.data.get(meta.collection)
    if not isinstance(records, list):
        return None
    labels = []
    visible = 0
    current = ""
    for row in records:
        if not isinstance(row, dict):
            continue
        value = row.get(meta.label_field)
        if not isinstance(value, str) or not value.strip():
            continue
        label = " ".join(value.split())[:100]
        visible += 1
        if len(labels) < _MAX_LABELS:
            labels.append(label)
        if meta.mark_current_workspace and row.get("id") == workspace:
            current = label
    if not labels:
        return f"{meta.title}: none visible."
    suffix = (
        " More entries may be available in Integral."
        if (
            visible > _MAX_LABELS
            or response.data.get("has_more") is True
            or bool(response.data.get("next_cursor"))
        )
        else ""
    )
    marker = f"The current workspace is {current}. " if current else ""
    return f"{marker}{meta.title}: {', '.join(labels)}.{suffix}"
