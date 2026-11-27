"""Server-owned, bounded catalogue offers for authenticated private voice reads."""

import asyncio
import re
import secrets

from app.agentive.tooling.manifest import ToolSpec

_MAX_OFFERS = 4


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


async def invoke_voice_read(spec: ToolSpec, *, principal: str, workspace: str):
    """Return a bounded page with server-held choices for the spoken list."""
    from app.services.native_voice_navigation import read_page

    return await read_page(spec, principal=principal, workspace=workspace)
