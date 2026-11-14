"""Pure catalog compilation; importing this module never initializes a server."""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path

from jvspatial.core import Edge, Node, Object
from jvspatial.db.indexes import IndexSpec
from jvspatial.db.postgres import PostgresDB

VERSION = 1


def model_classes():
    """Include non-graph Objects and framework models without importing app.main."""
    root = Path(__file__).resolve().parents[2]
    modules = {
        "app.models.nodes",
        "app.models.edges",
        "jvspatial.logging.models",
        "jvspatial.api.auth.models",
        "jvspatial.api.auth.oauth.models",
        "jvspatial.api.integrations.webhooks.models",
    }
    for folder in (
        root / "models",
        root / "agentive",
        root / "services" / "connectors",
    ):
        for path in sorted(folder.rglob("*.py")):
            if any(
                isinstance(n, ast.ClassDef)
                and any(
                    isinstance(b, ast.Name) and b.id in {"Object", "Node", "Edge"}
                    for b in n.bases
                )
                for n in ast.parse(path.read_text()).body
            ):
                modules.add(
                    "app." + ".".join(path.relative_to(root).with_suffix("").parts)
                )
    for name in sorted(modules):
        importlib.import_module(name)
    seen = set()

    def walk(base):
        for cls in base.__subclasses__():
            if cls in seen:
                continue
            seen.add(cls)
            yield cls
            yield from walk(cls)

    return sorted(
        (
            c
            for c in walk(Object)
            if c.__module__.startswith(("app.", "jvspatial."))
            and hasattr(c, "get_indexes")
        ),
        key=lambda c: (c.__module__, c.__name__),
    )


def compile_models(classes, *, schema="public") -> list[IndexSpec]:
    """Compile typed model declarations into explicit filter and ordering requirements."""
    db = PostgresDB(schema_name=schema, index_creation="managed")
    specs: dict[str, IndexSpec] = {}

    def put(spec):
        prior = specs.get(spec.fingerprint)
        if prior:
            spec = replace(
                spec,
                required=spec.required or prior.required,
                queries=tuple(sorted(set(spec.queries + prior.queries))),
            )
        specs[spec.fingerprint] = spec

    for cls in classes:
        table = (
            "edge"
            if issubclass(cls, Edge)
            else "node" if issubclass(cls, Node) else "object"
        )
        for index in cls.get_indexes():
            extra = {
                k: v
                for k, v in index.items()
                if k
                not in {"field", "fields", "unique", "direction", "per_class", "name"}
            }
            fields = index.get("fields") or [
                (index["field"], index.get("direction", 1))
            ]
            if index.get("per_class"):
                extra.update(
                    entity=cls._entity_name(),
                    partial_by_entity=not index.get("unique", False)
                    or index.get("partial_by_entity", False),
                )
            spec = db.index_spec(
                table, fields, unique=index.get("unique", False), **extra
            )
            spec = replace(
                spec,
                owner=cls.__module__,
                logical_id=f"{cls._entity_name()}:{spec.logical_id}",
                queries=(f"model:{cls._entity_name()}:{spec.logical_id}",),
            )
            put(spec)
            # Ordering is a distinct explicit catalog entry, never hidden DDL.
            if not spec.unique and spec.method == "btree":
                order = db.index_spec(table, fields, keyset=True, **extra).keys
                ordered = replace(
                    spec,
                    logical_id=spec.logical_id + ":keyset",
                    keys=order,
                    queries=(f"keyset:{cls._entity_name()}:{spec.logical_id}",),
                )
                put(ordered)
    return list(specs.values())


def control_specs(schema="public"):
    """Describe the integrity of the lifecycle metadata itself."""
    for table, key in (
        ("integral_index_catalog", "fingerprint"),
        ("integral_index_migrations", "plan_hash"),
    ):
        yield IndexSpec(
            table + ":primary",
            "core.storage",
            table,
            (key,),
            schema=schema,
            unique=True,
            required=True,
            queries=("storage:catalog-and-progress",),
            justification="Durable lifecycle metadata identity",
        )


def baseline_specs(schema="public"):
    """Describe shared collection identity and tenant access paths."""
    for table in ("object", "node", "edge"):
        yield IndexSpec(
            f"{table}:primary",
            "core.storage",
            table,
            ("id",),
            schema=schema,
            unique=True,
            required=True,
            justification="Collection identity constraint",
        )
        yield IndexSpec(
            f"{table}:entity",
            "core.storage",
            table,
            ("entity",),
            schema=schema,
            queries=("graph:entity-discovery",),
        )
        yield IndexSpec(
            f"{table}:tenant",
            "core.storage",
            table,
            ("tenant_id",),
            schema=schema,
            predicate="tenant_id IS NOT NULL",
            queries=("graph:tenant-scope",),
        )


def event_specs(schema="public"):
    """Describe authorized history and feed queries with exact native ordering."""
    db = PostgresDB(schema_name=schema)
    for direction in (-1, 1):
        for fields, predicate in (
            (
                [
                    ("context.log_level", 1),
                    ("context.log_data.workspace_id", 1),
                    ("context.logged_at", direction),
                    ("id", direction),
                ],
                {},
            ),
            (
                [
                    ("context.log_level", 1),
                    ("context.logged_at", direction),
                    ("id", direction),
                ],
                {"context.log_data.workspace_id": {"$exists": False}},
            ),
        ):
            spec = db.index_spec(
                "object",
                fields,
                entity="DBLog",
                partial_by_entity=True,
                index_partial_filter_expression=predicate,
            )
            yield replace(
                spec, owner="core.events", queries=("events:feed", "events:history")
            )
            ordering = db.index_spec(
                "object",
                fields,
                keyset=True,
                entity="DBLog",
                partial_by_entity=True,
                index_partial_filter_expression=predicate,
            )
            yield replace(
                ordering,
                logical_id=spec.logical_id + ":delivery-order",
                owner="core.events",
                queries=("events:feed", "events:history"),
                justification="Exact native feed ordering, including explicit ID direction",
            )
    spec = db.index_spec(
        "object",
        [
            ("context.log_level", 1),
            ("context.path", 1),
            ("context.logged_at", -1),
            ("id", -1),
        ],
        entity="DBLog",
        partial_by_entity=True,
    )
    yield replace(spec, owner="core.events", queries=("events:record-history",))
    ordering = db.index_spec(
        "object",
        [
            ("context.log_level", 1),
            ("context.path", 1),
            ("context.logged_at", -1),
            ("id", -1),
        ],
        keyset=True,
        entity="DBLog",
        partial_by_entity=True,
    )
    yield replace(
        ordering,
        logical_id=spec.logical_id + ":delivery-order",
        owner="core.events",
        queries=("events:record-history",),
        justification="Exact authorized record history ordering",
    )


def title_specs(schema="public"):
    """Describe bounded text pagination using the adapter compiler."""
    db = PostgresDB(schema_name=schema)
    for spec in db.binary_text_keyset_specs(
        "node", field="context.title", scope_field="context.track_id", entity="Entry"
    ):
        kind = spec.logical_id.split(":")[-1]
        yield replace(
            spec,
            logical_id="entry:title:" + kind,
            owner="core.pagination",
            queries=(
                (
                    "entries:title-fallback"
                    if kind == "malformed"
                    else "entries:title-page"
                ),
            ),
        )


def queue_specs(schema="public"):
    """Raw durable records have no Object annotations to discover."""
    db = PostgresDB(schema_name=schema)
    for fields in (
        [("context.status", 1), ("context.updated_at", 1), ("id", 1)],
        [("context.status", 1), ("context.next_attempt_at", 1)],
        [("context.status", 1), ("context.lease_until", 1)],
        [("context.created_at", 1), ("id", 1)],
    ):
        spec = db.index_spec(
            "object", fields, entity="OperationEventOutbox", partial_by_entity=True
        )
        yield replace(
            spec,
            owner="core.events",
            queries=("event-worker:due", "event-worker:health"),
        )
    for fields, entity, key, owner, query in (
        (
            [("context.updated_at", 1), ("id", 1)],
            "OperationEventOutbox",
            "event-outbox:delivery-order",
            "core.events",
            "event-worker:due",
        ),
        (
            [("context.workspace_id", 1), ("context.due", 1), ("id", 1)],
            "EntryIndexJob",
            "entry-index-job:fair-order",
            "core.search",
            "index-worker:due",
        ),
    ):
        spec = db.index_spec(
            "object", fields, keyset=True, entity=entity, partial_by_entity=True
        )
        yield replace(spec, logical_id=key, owner=owner, queries=(query,))
    yield IndexSpec(
        "entry-index-job:due-filter",
        "core.search",
        "object",
        ("(data #>> '{context,status}')", "((data #>> '{context,due}')::numeric)"),
        schema=schema,
        predicate="entity = 'EntryIndexJob'",
        queries=("index-worker:due",),
        justification="Core-owned durable job schema stores numeric due timestamps",
    )


def proposed_catalog(*, schema="public", classes=None, include_search=True):
    """Collect candidate requirements before release approval is applied."""
    specs = [
        *control_specs(schema),
        *baseline_specs(schema),
        *compile_models(
            classes if classes is not None else model_classes(), schema=schema
        ),
        *event_specs(schema),
        *title_specs(schema),
        *queue_specs(schema),
    ]
    if include_search:
        from .search import search_specs

        specs.extend(search_specs(schema=schema))
    result = {}
    for spec in specs:
        prior = result.get(spec.fingerprint)
        if prior:
            spec = replace(
                spec,
                owner=prior.owner if prior.owner == "core.storage" else spec.owner,
                logical_id=(
                    prior.logical_id
                    if prior.owner == "core.storage"
                    else spec.logical_id
                ),
                required=spec.required or prior.required,
                queries=tuple(sorted(set(spec.queries + prior.queries))),
            )
        result[spec.fingerprint] = spec
    return sorted(result.values(), key=lambda s: s.fingerprint)


def revision(specs):
    """Bind all physical definitions and ownership/query metadata to one revision."""
    return hashlib.sha256(
        json.dumps([s.to_dict() for s in specs], sort_keys=True).encode()
    ).hexdigest()


def collection_sql(schema):
    """Return reviewed baseline relation definitions for maintenance execution."""
    from jvspatial.db.indexes import identifier as q

    return [
        f"CREATE TABLE IF NOT EXISTS {q(schema)}.{q(table)} (id TEXT PRIMARY KEY, entity TEXT NOT NULL, "
        "tenant_id TEXT, data JSONB NOT NULL, _v INTEGER NOT NULL DEFAULT 1, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        for table in ("object", "node", "edge")
    ]


def build_catalog(*, schema="public", classes=None, include_search=True):
    """Only versioned, reviewed fingerprints enter the serving catalog.

    New model declarations remain proposals. New integrity requirements fail
    compilation until the release catalog is updated deliberately.
    """
    candidates = proposed_catalog(
        schema=schema, classes=classes, include_search=include_search
    )
    if classes is not None:
        return candidates
    approval = json.loads(Path(__file__).with_name("approved.json").read_text())
    if approval["version"] != VERSION:
        raise ValueError("Unsupported storage catalog version")
    allowed = {item["fingerprint"] for item in approval["indexes"]}
    result = []
    for spec in candidates:
        approved = replace(spec, schema="public").fingerprint in allowed
        if spec.required and not approved:
            raise ValueError(
                "New integrity declaration requires a reviewed catalog revision"
            )
        if approved:
            result.append(spec)
    for binding in filter(
        None, os.getenv("INTEGRAL_STORAGE_CATALOG_PROVIDERS", "").split(",")
    ):
        if not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*", binding.strip()
        ):
            raise ValueError("Invalid trusted storage catalog provider")
        module, function = binding.strip().split(":")
        provider = getattr(importlib.import_module(module), function)()
        if provider["version"] != 1 or not provider.get("owner"):
            raise ValueError("Unsupported trusted provider catalog")
        approved = set(provider["approved_fingerprints"])
        for spec in compile_models(provider["models"], schema=schema):
            if replace(spec, schema="public").fingerprint not in approved:
                if spec.required:
                    raise ValueError(
                        "Host integrity declaration requires a reviewed provider catalog"
                    )
                continue
            result.append(
                replace(spec, owner=provider["owner"], version=str(provider["version"]))
            )
    dedup = {}
    for spec in result:
        prior = dedup.get(spec.fingerprint)
        if prior:
            spec = replace(
                spec,
                required=spec.required or prior.required,
                queries=tuple(sorted(set(spec.queries + prior.queries))),
            )
        dedup[spec.fingerprint] = spec
    return sorted(dedup.values(), key=lambda s: s.fingerprint)
