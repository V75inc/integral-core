"""Graph contract checks for native Harness continuity sessions."""

from jvspatial.core import Node

from app.models.edges import HAS_HARNESS_SESSION, HasHarnessSession
from app.models.nodes import HarnessSession


def test_harness_session_is_a_rooted_chat_thread_graph_participant() -> None:
    """Harness continuity is a first-class child of its owning ChatThread."""
    assert issubclass(HarnessSession, Node)
    assert HAS_HARNESS_SESSION is HasHarnessSession
    assert {"session_id", "thread_id", "workspace_id", "principal_id"} <= set(
        HarnessSession.model_fields
    )
    assert {"binding_id", "generation", "bound_at"} <= set(
        HasHarnessSession.model_fields
    )
