"""Index maintenance proposals retain the normal approval and scope boundary."""

from app.services.retrieval.index_definition import IndexDefinition
from app.services.retrieval.index_management import admit_track


async def stage_index(args: dict, *, configure: bool) -> dict:
    """Validate current authority and build an approval proposal."""
    from app.agentive.tooling.bindings import _bound_propose_principal

    track = await admit_track(
        _bound_propose_principal(), str(args.get("track_id") or "")
    )
    payload = {"track_id": track.id}
    if configure:
        payload["expected_revision"] = track.search_index_revision
        payload["definition"] = IndexDefinition.model_validate(
            args.get("definition")
        ).model_dump()
    summary = ("Configure search for " if configure else "Reindex ") + track.title
    human = summary
    if configure:
        definition = payload["definition"]

        def labels(paths):
            names = {
                "title": "Titles",
                "body": "Content",
                "custom_fields.*": "Other text fields",
            }
            return (
                ", ".join(
                    names.get(p, p.removeprefix("custom_fields.").replace("_", " "))
                    for p in paths
                )
                or "None"
            )

        human += (
            f"\nSearch: {'On' if definition['enabled'] else 'Off'}"
            f"\nInclude: {labels(definition['fields'])}"
            f"\nExclude: {labels(definition['exclude_fields'])}"
            f"\nEntry types: {len(definition['entry_type_ids']) or 'All'}"
            f"\nMetadata: {labels(definition['metadata_fields'])}"
        )
    return {
        "kind": "configure_search_index" if configure else "reindex_track",
        "summary": summary,
        "diff_human": human,
        "diff_machine": {
            "before": track.search_index,
            "after": payload["definition"] if configure else track.search_index,
        },
        "payload": payload,
    }


async def stage_configure_index(args: dict) -> dict:
    """Propose a complete index declaration."""
    return await stage_index(args, configure=True)


async def stage_reindex(args: dict) -> dict:
    """Propose rebuilding the current profile."""
    return await stage_index(args, configure=False)
