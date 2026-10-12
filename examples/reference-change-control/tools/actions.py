"""External reference transition; all authority comes from the Core facade."""


async def release(payload, ctx):
    """Apply one conditional protected-state transition."""
    entry_id = payload["entry_id"]
    ok, error = await ctx.conditional_update_entry_fields(
        entry_id,
        state_field="release_state",
        expected_state="pending",
        updates={"release_state": "released"},
    )
    if not ok:
        return {"changed": False, "reason": error or "denied"}
    return {"changed": True, "object_refs": [{"kind": "entry", "id": entry_id}]}


async def edit(payload, ctx):
    changed = await ctx.update_entry_fields(
        payload["entry_id"], {"editor_note": payload["value"]}
    )
    return {"changed": changed}


async def configure(payload, ctx):
    changed = await ctx.update_entry_fields(
        payload["entry_id"], {"routing_configuration": payload["value"]}
    )
    return {"changed": changed}
