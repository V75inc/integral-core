"""App declared queries package."""

from app.services.app_queries.dispatch import (
    invoke_app_query,
    list_app_queries,
    sync_app_queries_from_manifest,
)
from app.services.app_queries.registry import (
    get_app_query,
    list_registered_queries,
    register_app_queries,
    unregister_app_queries,
)

__all__ = [
    "invoke_app_query",
    "list_app_queries",
    "sync_app_queries_from_manifest",
    "get_app_query",
    "list_registered_queries",
    "register_app_queries",
    "unregister_app_queries",
]
