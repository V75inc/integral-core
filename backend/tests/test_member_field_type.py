"""ACC-08 — `member` field-type tests.

Covers:
- Registry registration: `member` appears in ``allowed_keys()`` with the
  documented label/description.
- Validator: ``validate_member_value`` accepts a User in the entry's
  Workspace, rejects a User outside it (BadRequestError), and rejects
  empty / unknown User ids.
- Materialization: ``_sync_member_ref_edges`` wires the
  ``HAS_MEMBER_REF`` edge ``Entry → User`` with the correct
  ``field_key`` associative metadata; re-runs upsert (single edge per
  (entry, field_key) pair); the scalar User-id cache flows through the
  ``validate_and_materialize_entry_custom_fields`` → ``sync_relation_edges``
  pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.errors import BadRequestError
from app.models.edges import HAS_MEMBER_REF, IS_MEMBER_OF, HasMemberRef
from app.models.nodes import Entry, Track, User, Workspace
from app.services.content_profile_field_types import allowed_keys, get
from app.services.content_profile_graph import _sync_member_ref_edges
from app.services.content_profile_member_field import validate_member_value


async def _wire_member(
    *, user: User, workspace: Workspace, role: str = "member"
) -> None:
    """Create IS_MEMBER_OF edge user → workspace."""
    now = datetime.now(timezone.utc).isoformat()
    await user.connect(
        workspace,
        edge=IS_MEMBER_OF,
        role=role,
        joined_at=now,
    )


async def _bootstrap_workspace_fixture() -> dict:
    """Provision the canonical ACC-08 test fixture.

    Returns a dict with ``workspace``, ``track``, ``entry``, ``member_user``,
    ``outsider_user`` so the per-test setup stays uniform.
    """
    workspace = await Workspace.create(
        name="Test Org",
        kind="organization",
    )
    track = await Track.create(
        title="Tasks",
        owner_id="member-test-owner",
        workspace_id=workspace.id,
    )
    entry = await Entry.create(
        track_id=track.id,
        title="Test Task",
        author_id="member-test-owner",
    )
    member_user = await User.create(
        user_id="auth-member-1",
        display_name="Member One",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    outsider_user = await User.create(
        user_id="auth-outsider-1",
        display_name="Outsider One",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await _wire_member(user=member_user, workspace=workspace)
    return {
        "workspace": workspace,
        "track": track,
        "entry": entry,
        "member_user": member_user,
        "outsider_user": outsider_user,
    }


# ----- Registry ---------------------------------------------------------------


def test_member_field_type_registered():
    """member appears in allowed_keys() after module import."""
    keys = allowed_keys()
    assert "member" in keys, sorted(keys)


def test_member_field_type_spec_metadata():
    """member spec carries the documented label + description + base=relation."""
    spec = get("member")
    assert spec is not None
    assert spec.type == "member"
    assert spec.base == "relation"
    assert spec.label == "Member"
    assert "workspace member" in spec.description.lower()


# ----- Agent introspection (describe_substrate) ------------------------------


@pytest.mark.asyncio
async def test_describe_substrate_surfaces_member_field_type():
    """integral_describe_substrate exposes `member` in field_types."""
    from app.services.agent_profiles import describe_substrate

    out = await describe_substrate()
    member = next((f for f in out["field_types"] if f["type"] == "member"), None)
    assert member is not None, [f["type"] for f in out["field_types"]]
    assert member["base"] == "relation"
    assert member["label"] == "Member"
    assert "workspace member" in member["description"].lower()


@pytest.mark.asyncio
async def test_describe_substrate_enumerates_has_member_ref_edge():
    """integral_describe_substrate `edges` enumeration includes HAS_MEMBER_REF."""
    from app.services.agent_profiles import describe_substrate

    out = await describe_substrate()
    assert "HAS_MEMBER_REF" in out["edges"], list(out["edges"].keys())
    entry = out["edges"]["HAS_MEMBER_REF"]
    assert entry["source"] == "Entry"
    assert entry["target"] == "User"
    assert "member" in entry["via"].lower()


@pytest.mark.asyncio
async def test_describe_substrate_legacy_keys_unchanged_after_member_addition():
    """describe_substrate additive-extension invariant (INVARIANTS.md L422).

    Legacy keys (`field_types`, `view_types`, `relation_targets`, etc.)
    keep their pre-ACC-08 shape. The `member` addition appends inside
    `field_types`; no existing key is renamed or has its value type
    changed.
    """
    from app.services.agent_profiles import describe_substrate

    out = await describe_substrate()
    for required in (
        "field_types",
        "view_types",
        "plugins",
        "registry_versions",
        "relation_targets",
        "edges",
        "template_var_resolvers",
        "governance_actions",
    ):
        assert required in out, f"describe_substrate legacy key missing: {required}"
    # relation_targets is NOT extended for `member` — `member` is its own
    # field type, not a `relation` variant. Confirm the literal is intact.
    assert sorted(out["relation_targets"]) == ["entry", "track"], out[
        "relation_targets"
    ]


# ----- Validator --------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_member_value_accepts_workspace_member():
    """A User who IS_MEMBER_OF the workspace is accepted."""
    fx = await _bootstrap_workspace_fixture()
    out = await validate_member_value(
        fx["member_user"].id,
        field_key="assignee",
        source_track=fx["track"],
        source_entry=fx["entry"],
    )
    assert out == fx["member_user"].id


@pytest.mark.asyncio
async def test_validate_member_value_rejects_outsider():
    """A User NOT in the workspace is rejected with BadRequestError."""
    fx = await _bootstrap_workspace_fixture()
    with pytest.raises(BadRequestError) as exc_info:
        await validate_member_value(
            fx["outsider_user"].id,
            field_key="assignee",
            source_track=fx["track"],
            source_entry=fx["entry"],
        )
    msg = str(exc_info.value)
    assert "not a member" in msg.lower() or "workspace" in msg.lower()


@pytest.mark.asyncio
async def test_validate_member_value_rejects_unknown_user_id():
    """An unknown User id is rejected."""
    fx = await _bootstrap_workspace_fixture()
    with pytest.raises(BadRequestError):
        await validate_member_value(
            "no-such-user-id-12345",
            field_key="assignee",
            source_track=fx["track"],
            source_entry=fx["entry"],
        )


@pytest.mark.asyncio
async def test_validate_member_value_rejects_none_and_empty():
    """None and empty-string values are rejected."""
    fx = await _bootstrap_workspace_fixture()
    with pytest.raises(BadRequestError):
        await validate_member_value(
            None,
            field_key="assignee",
            source_track=fx["track"],
        )
    with pytest.raises(BadRequestError):
        await validate_member_value(
            "",
            field_key="assignee",
            source_track=fx["track"],
        )


# ----- Materialization (_sync_member_ref_edges) -------------------------------


@pytest.mark.asyncio
async def test_sync_member_ref_edges_writes_has_member_ref():
    """A single member_ref write materializes HAS_MEMBER_REF Entry → User."""
    fx = await _bootstrap_workspace_fixture()
    await _sync_member_ref_edges(
        source_entry=fx["entry"],
        relation_refs=[
            {
                "field_key": "assignee",
                "targets": [fx["member_user"].id],
                "target": "user",
            }
        ],
    )
    users_out = await fx["entry"].nodes(
        edge=["HAS_MEMBER_REF"], direction="out", node=["User"]
    )
    assert len(users_out) == 1
    assert users_out[0].id == fx["member_user"].id


@pytest.mark.asyncio
async def test_sync_member_ref_edges_carries_field_key_metadata():
    """The HAS_MEMBER_REF edge stores `field_key` as a first-class typed field."""
    fx = await _bootstrap_workspace_fixture()
    await _sync_member_ref_edges(
        source_entry=fx["entry"],
        relation_refs=[
            {
                "field_key": "assignee",
                "targets": [fx["member_user"].id],
                "target": "user",
            }
        ],
    )
    ctx = await fx["entry"].get_context()
    edges = await ctx.find_edges_between(
        fx["entry"].id, fx["member_user"].id, edge_class=HasMemberRef
    )
    assert len(edges) == 1
    assert getattr(edges[0], "field_key", None) == "assignee"


@pytest.mark.asyncio
async def test_sync_member_ref_edges_idempotent_replace_semantics():
    """Calling the helper twice for the same (entry, field_key) yields ONE edge."""
    fx = await _bootstrap_workspace_fixture()
    refs = [
        {
            "field_key": "assignee",
            "targets": [fx["member_user"].id],
            "target": "user",
        }
    ]
    await _sync_member_ref_edges(source_entry=fx["entry"], relation_refs=refs)
    await _sync_member_ref_edges(source_entry=fx["entry"], relation_refs=refs)
    users_out = await fx["entry"].nodes(
        edge=["HAS_MEMBER_REF"], direction="out", node=["User"]
    )
    ctx = await fx["entry"].get_context()
    edges = await ctx.find_edges_between(
        fx["entry"].id, fx["member_user"].id, edge_class=HasMemberRef
    )
    assert len(users_out) == 1
    assert len(edges) == 1


@pytest.mark.asyncio
async def test_sync_member_ref_edges_rewrite_to_different_user():
    """Rewriting `assignee` to a different User replaces the edge (1 edge in total)."""
    fx = await _bootstrap_workspace_fixture()
    # First write: assignee → member_user
    await _sync_member_ref_edges(
        source_entry=fx["entry"],
        relation_refs=[
            {
                "field_key": "assignee",
                "targets": [fx["member_user"].id],
                "target": "user",
            }
        ],
    )
    # Provision a second workspace member.
    second_member = await User.create(
        user_id="auth-member-2",
        display_name="Member Two",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await _wire_member(user=second_member, workspace=fx["workspace"])
    # Rewrite: assignee → second_member
    await _sync_member_ref_edges(
        source_entry=fx["entry"],
        relation_refs=[
            {
                "field_key": "assignee",
                "targets": [second_member.id],
                "target": "user",
            }
        ],
    )
    users_out = await fx["entry"].nodes(
        edge=["HAS_MEMBER_REF"], direction="out", node=["User"]
    )
    assert len(users_out) == 1
    assert users_out[0].id == second_member.id


@pytest.mark.asyncio
async def test_sync_member_ref_edges_separate_field_keys_coexist():
    """Two `member` fields on the same entry produce two distinct HAS_MEMBER_REF edges."""
    fx = await _bootstrap_workspace_fixture()
    reviewer = await User.create(
        user_id="auth-reviewer-1",
        display_name="Reviewer",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await _wire_member(user=reviewer, workspace=fx["workspace"])
    await _sync_member_ref_edges(
        source_entry=fx["entry"],
        relation_refs=[
            {
                "field_key": "assignee",
                "targets": [fx["member_user"].id],
                "target": "user",
            },
            {
                "field_key": "reviewer",
                "targets": [reviewer.id],
                "target": "user",
            },
        ],
    )
    users_out = await fx["entry"].nodes(
        edge=["HAS_MEMBER_REF"], direction="out", node=["User"]
    )
    assert len(users_out) == 2


# ----- End-to-end pipeline via sync_relation_edges ----------------------------


@pytest.mark.asyncio
async def test_sync_relation_edges_routes_member_target_to_member_helper():
    """relation_refs[target='user'] routes through _sync_member_ref_edges."""
    from app.services.content_profile_runtime import sync_relation_edges

    fx = await _bootstrap_workspace_fixture()
    await sync_relation_edges(
        source_entry=fx["entry"],
        relation_refs=[
            {
                "field_key": "assignee",
                "targets": [fx["member_user"].id],
                "target": "user",
            }
        ],
    )
    users_out = await fx["entry"].nodes(
        edge=["HAS_MEMBER_REF"], direction="out", node=["User"]
    )
    assert len(users_out) == 1
    assert users_out[0].id == fx["member_user"].id


# ----- Single-writer grep gate ------------------------------------------------


def test_has_member_ref_write_path_grep_gate():
    """ACC-08 — HAS_MEMBER_REF writes occur ONLY inside _sync_member_ref_edges.

    Mirrors the ANCHORS gate at `test_anchor_edge.test_anchors_write_path_grep_gate`.
    """
    import subprocess
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    cmd = (
        f'grep -rE "edge=HAS_MEMBER_REF|edge=HasMemberRef\\b" '
        f'{repo_root}/backend/app/ --include="*.py" '
        f"| grep -v __pycache__"
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    offenders = []
    sanctioned_helper = "_sync_member_ref_edges"
    # Sanctioned file: content_profile_graph.py — that's where
    # _sync_member_ref_edges lives. The function-body match is also OK
    # so long as the file is the sanctioned one.
    sanctioned_suffixes = ("app/services/content_profile_graph.py",)
    for ln in result.stdout.strip().split("\n"):
        if not ln:
            continue
        try:
            path, content = ln.split(":", 1)
        except ValueError:
            continue
        prefix = content.split("edge=", 1)[0]
        if "#" in prefix or content.lstrip().startswith("#"):
            continue
        # Drop docstring example lines — actual edge writes always sit on a
        # ``.connect(`` callsite line in real code. Docstring examples
        # mentioning the literal ``edge=HAS_MEMBER_REF`` are framed in
        # double-backticks (Sphinx convention) AND never appear on a real
        # source line. Filter by both signals: a real call must contain
        # ``.connect(`` AND must NOT have a backtick before it on the line.
        if ".connect(" not in content:
            continue
        before_connect = content.split(".connect(", 1)[0]
        if "`" in before_connect:
            continue
        if not any(path.endswith(s) for s in sanctioned_suffixes):
            offenders.append(ln)
    assert not offenders, (
        f"HAS_MEMBER_REF edge writes are restricted to "
        f"{sanctioned_helper}; offenders:\n" + "\n".join(offenders)
    )


@pytest.mark.asyncio
async def test_validate_plural_member_field():
    """Verify validate_and_materialize_entry_custom_fields handles plural member fields correctly."""
    from app.models.nodes import EntryType
    from app.services.content_profile_entry_fields import (
        validate_and_materialize_entry_custom_fields,
    )

    fx = await _bootstrap_workspace_fixture()

    # Create another member to have multiple members
    second_member = await User.create(
        user_id="auth-member-2",
        display_name="Member Two",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await _wire_member(user=second_member, workspace=fx["workspace"])

    # Define an EntryType with a plural member field
    entry_type = await EntryType.create(
        name="PluralMemberEntryType",
        form_schema={
            "fields": [
                {
                    "key": "assignees",
                    "name": "Assignees",
                    "type": "member",
                    "relation": {"many": True},
                }
            ]
        },
    )

    # Validate field values containing a list of User IDs
    out_cf, relation_refs = await validate_and_materialize_entry_custom_fields(
        track=fx["track"],
        entry_type=entry_type,
        custom_fields={"assignees": [fx["member_user"].id, second_member.id]},
        runtime_tier={},
    )

    assert out_cf["assignees"] == [fx["member_user"].id, second_member.id]
    assert len(relation_refs) == 1
    assert relation_refs[0]["field_key"] == "assignees"
    assert relation_refs[0]["targets"] == [fx["member_user"].id, second_member.id]
    assert relation_refs[0]["many"] is True
