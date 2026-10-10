"""Resolve vendor labels only through the bound destination's declared tags."""

from __future__ import annotations

from app.models.nodes import Tag, Track
from app.services.query_boundary import parent_app_for_track


async def resolve_connector_tags(track: Track, references: list[str]) -> list[str]:
    """Accept scoped stable IDs or unambiguous declared names/aliases."""
    if not references:
        return []
    app_node = await parent_app_for_track(track)
    query = {"context.track_id": track.id}
    if app_node:
        query = {
            "$or": [query, {"context.app_id": app_node.id, "context.track_id": ""}]
        }
    graph = await track.get_context()
    tags = await graph.find(Tag, query, limit=1001)
    if len(tags) > 1000:
        raise ValueError("Connector taxonomy exceeds the supported lookup limit")
    resolved = []
    for reference in references:
        exact = [tag for tag in tags if tag.id == reference]
        matches = exact or [
            tag
            for tag in tags
            if str(reference).casefold()
            in {str(name).casefold() for name in [tag.name, *(tag.aliases or [])]}
        ]
        if len(matches) != 1:
            raise ValueError(
                "Connector tag is unknown or ambiguous in its declared taxonomy"
            )
        resolved.append(matches[0].id)
    return list(dict.fromkeys(resolved))
