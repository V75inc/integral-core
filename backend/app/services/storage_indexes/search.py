"""Approved search storage shapes, independent of runtime query execution."""

from jvspatial.db.indexes import IndexSpec, identifier


def search_schema_sql(schema="public", *, table="entry_embedding", dim=384):
    """Describe reviewed derived lexical and vector storage shapes."""
    if not 16 <= dim <= 2000:
        raise ValueError("Invalid embedding dimension")

    def q(name):
        return f"{identifier(schema)}.{identifier(name)}"

    return [
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"CREATE TABLE IF NOT EXISTS {q(table)} (entry_id text PRIMARY KEY, track_id text NOT NULL, "
        f"type_id text, vector vector({dim}) NOT NULL, deleted_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now())",
        f"CREATE TABLE IF NOT EXISTS {q(table+'_passage')} (entry_id text NOT NULL, profile_id text NOT NULL, ordinal integer NOT NULL, "
        "track_id text NOT NULL, workspace_id text NOT NULL, app_id text NOT NULL, source_hash text NOT NULL, source_revision bigint NOT NULL, "
        "field_path text NOT NULL, start_offset integer NOT NULL, end_offset integer NOT NULL, content text NOT NULL, "
        "metadata jsonb NOT NULL DEFAULT '{}', embedding vector NOT NULL, deleted_at timestamptz, PRIMARY KEY(entry_id,profile_id,ordinal))",
        f"CREATE TABLE IF NOT EXISTS {q(table+'_lexical')} (entry_id text NOT NULL, ordinal integer NOT NULL, "
        "track_id text NOT NULL, workspace_id text NOT NULL, app_id text NOT NULL, source_hash text NOT NULL, source_revision bigint NOT NULL, "
        "field_path text NOT NULL, start_offset integer NOT NULL, end_offset integer NOT NULL, content text NOT NULL, metadata jsonb NOT NULL DEFAULT '{}', "
        "search_terms tsvector GENERATED ALWAYS AS (setweight(to_tsvector('simple'::regconfig,content), "
        "CASE WHEN field_path='title' THEN 'A'::\"char\" ELSE 'D'::\"char\" END)) STORED, "
        "deleted_at timestamptz, PRIMARY KEY(entry_id,ordinal))",
        f"CREATE TABLE IF NOT EXISTS {q(table+'_manifest')} (entry_id text NOT NULL, profile_id text NOT NULL, "
        "source_hash text NOT NULL, passage_count integer NOT NULL, checksum text NOT NULL, published_at timestamptz NOT NULL DEFAULT now(), "
        "PRIMARY KEY(entry_id,profile_id))",
    ]


def search_specs(*, schema="public", table="entry_embedding"):
    """Describe shared lexical and fixed-profile vector access paths."""

    def spec(key, relation, keys, **kwargs):
        return IndexSpec(
            key,
            "core.search",
            relation,
            tuple(keys),
            schema=schema,
            queries=("retrieval:" + key,),
            **kwargs,
        )

    for relation, keys in (
        (table, ["entry_id"]),
        (table + "_passage", ["entry_id", "profile_id", "ordinal"]),
        (table + "_lexical", ["entry_id", "ordinal"]),
        (table + "_manifest", ["entry_id", "profile_id"]),
    ):
        yield spec(relation + ":primary", relation, keys, unique=True)
    yield spec("legacy-track", table, ["track_id"])
    yield spec(
        "legacy-ann",
        table,
        ["vector vector_cosine_ops"],
        method="hnsw",
        justification="Existing fixed-dimension embedding provider",
    )
    yield spec(
        "passage-scope",
        table + "_passage",
        ["profile_id", "workspace_id", "app_id", "track_id"],
    )
    yield spec(
        "lexical-text",
        table + "_lexical",
        ["search_terms"],
        method="gin",
        predicate="deleted_at IS NULL",
    )
    yield spec(
        "lexical-scope",
        table + "_lexical",
        ["workspace_id", "app_id", "track_id"],
        predicate="deleted_at IS NULL",
    )
    for relation, keys in (
        (
            table + "_passage",
            [
                "profile_id",
                "workspace_id",
                "(metadata->>'_integral_type_id')",
                "track_id",
            ],
        ),
        (
            table + "_lexical",
            ["workspace_id", "(metadata->>'_integral_type_id')", "track_id"],
        ),
    ):
        yield spec(relation + ":type", relation, keys, predicate="deleted_at IS NULL")
    for relation in (table + "_passage", table + "_lexical"):
        keys = (["profile_id"] if relation.endswith("_passage") else []) + [
            "workspace_id",
            "(metadata->>'_integral_attachment_kind')",
            "entry_id",
        ]
        yield spec(
            relation + ":attachment",
            relation,
            keys,
            predicate="deleted_at IS NULL AND field_path LIKE 'attachments.%'",
        )


def profile_ann_spec(profile, *, schema="public", table="entry_embedding_passage"):
    # The embedding profile parser validates a content-addressed ID and dimensions.
    """Describe one explicitly reviewed embedding profile without tenant auto-builds."""
    if not isinstance(profile.id, str) or any(
        x in profile.id for x in ("'", ";", "--")
    ):
        raise ValueError("Invalid embedding profile identity")
    if (
        isinstance(profile.dimensions, bool)
        or not isinstance(profile.dimensions, int)
        or not 16 <= profile.dimensions <= 2000
    ):
        raise ValueError("Invalid embedding profile dimension")
    return IndexSpec(
        "profile-ann:" + profile.id,
        "core.search",
        table,
        (f"(embedding::vector({int(profile.dimensions)})) vector_cosine_ops",),
        schema=schema,
        method="hnsw",
        predicate=f"profile_id='{profile.id}' AND deleted_at IS NULL",
        queries=("retrieval:semantic",),
        justification="Explicitly reviewed shared embedding profile",
    )
