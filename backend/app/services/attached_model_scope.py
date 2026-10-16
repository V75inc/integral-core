"""Fresh selection of an attached model, never a scalar-cache capability."""

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import HAS_OPERATIONAL_MODEL
from app.models.nodes import App, OperationalModel, Track
from app.services.authority_facts import required_node_text
from app.services.model_authority import get_current_model
from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource


async def read_attached_model(
    kind: str, owner_id: str
) -> tuple[App | Track, OperationalModel] | None:
    """Compose canonical owner location and one bounded attachment lookup.

    Selection is not authorization. The caller must check current model
    authority for its requested action before consuming or changing the model.
    """
    records: dict[str, dict] = {}
    location = await locate_resource(
        kind, owner_id, allowed_app_states=MANAGEMENT_APP_STATES, records_out=records
    )
    if location is None:
        return None
    context = get_default_context()
    try:
        edges = await context.database.find(
            "edge",
            {"entity": HAS_OPERATIONAL_MODEL._entity_name(), "source": owner_id},
            limit=2,
        )
        if len(edges) != 1:
            return None
        edge = edges[0]
        if (
            edge.get("source") != owner_id
            or edge.get("entity") != HAS_OPERATIONAL_MODEL._entity_name()
            or not isinstance(edge.get("target"), str)
            or not edge["target"]
        ):
            raise AuthorizationUnavailableError()
        if (
            required_node_text(records[owner_id], "attached_operational_model_id")
            != edge["target"]
        ):
            return None
        model = await get_current_model(edge["target"])
        if model is None:
            return None
        owner = await context._deserialize_entity(
            {"track": Track, "app": App}[kind], records[owner_id]
        )
        if owner is None:
            raise AuthorizationUnavailableError()
        return owner, model
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc
