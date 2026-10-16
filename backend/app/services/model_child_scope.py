"""Exact structural membership for children of an already authorized model."""

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CONTAINS
from app.models.nodes import EntryType, Tag
from app.services.authority_facts import required_node_text
from app.services.resource_location import ResourceLocationWalker


async def read_model_child(
    model_id: str,
    child_id: str,
    kind: str,
    *,
    track_id: str = "",
    app_id: str = "",
) -> EntryType | Tag | None:
    """Require a unique named parent; scalar caches never establish ownership.

    The caller must authorize the current model independently. This helper
    supplies no model, workspace, or record permission of its own.
    """
    model = {"entry_type": EntryType, "tag": Tag}[kind]
    context = get_default_context()
    try:
        reader = ResourceLocationWalker()
        child = await reader.read_node(model, child_id, context)
        if child is None:
            return None
        parents = await reader.parent_rows(child_id, CONTAINS, 1, context)
        if len(parents) != 1 or parents[0]["source"] != model_id:
            return None
        row = reader._records[child_id]
        for field, expected in (("track_id", track_id), ("app_id", app_id)):
            if field == "app_id" and kind == "entry_type":
                continue
            declared = required_node_text(row, field, allow_empty=True)
            if declared and declared != expected:
                return None
        return child
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc
