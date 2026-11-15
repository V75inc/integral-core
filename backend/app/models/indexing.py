"""Index declarations for Core's own models: optional per-record-kind (partial) indexes.

jvspatial builds, for every ``attribute(..., indexed=True)``, an index that leads with
the entity column and covers EVERY row in the shared object/node tables, so each write of
any kind pays for every index of every other kind (129 indexes on the object table of the
benchmark database). With ``INTEGRAL_PARTIAL_INDEXES=1`` Core's declarations ask for the
per-kind form instead (``WHERE entity = '<Kind>'``): the same queries use them (plan and
timing measured in docs/backend/index-migration.md) while a write only touches the indexes
of its own kind. Off by default: turning it on is the "expand" step, which ADDS the
partial indexes beside the existing ones; nothing is dropped until the later "remove"
step (see the migration plan). Unique indexes are left exactly as declared.
"""

from __future__ import annotations

import os
from typing import Any

from jvspatial.core.annotations import attribute as _attribute
from jvspatial.core.annotations import compound_index as _compound_index

_ON = {"1", "true", "yes", "on"}


def partial_indexes_enabled() -> bool:
    """Read the switch at declaration time (model import)."""
    return os.getenv("INTEGRAL_PARTIAL_INDEXES", "").strip().lower() in _ON


def attribute(*args: Any, **kwargs: Any) -> Any:
    """``jvspatial.core.annotations.attribute`` with per-kind indexes when enabled."""
    if (
        kwargs.get("indexed")
        and not kwargs.get("index_unique")
        and "index_partial_by_entity" not in kwargs
        and partial_indexes_enabled()
    ):
        kwargs["index_partial_by_entity"] = True
    return _attribute(*args, **kwargs)


def compound_index(fields: Any, **kwargs: Any) -> Any:
    """``compound_index`` with per-kind indexes when enabled (unique ones unchanged)."""
    if (
        not kwargs.get("unique")
        and "partial_by_entity" not in kwargs
        and partial_indexes_enabled()
    ):
        kwargs["partial_by_entity"] = True
    return _compound_index(fields, **kwargs)
