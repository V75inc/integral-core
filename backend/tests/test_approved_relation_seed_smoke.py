"""Named relation seeds survive binding without weakening approved values."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agentive.staging import _reset_for_tests
from app.agentive.tooling import scaffold_build
from app.agentive.tooling.dispatch import ToolResult
from app.services.design_blueprint import plan_fidelity_errors, validate_blueprint

pytestmark = pytest.mark.smoke


def scenario():
    companies = [
        "Berghotel Grosse Scheidegg",
        "Amazon",
        "Walmart",
        "McDonald’s",
        "Shell",
    ]
    expenses = [
        ("groceries at Walmart", 120, "Walmart"),
        ("dinner at McDonald’s", 35, "McDonald’s"),
        ("Amazon purchase", 85, "Amazon"),
        ("fuel at Shell", 60, "Shell"),
        ("hotel expenses at Berghotel Grosse Scheidegg", 200, companies[0]),
        ("coffee purchase", 8, None),
    ]
    relation = {
        "key": "business",
        "name": "Business",
        "type": "relation",
        "relation": {
            "target": "entry",
            "target_entry_types": ["Business"],
            "target_track_types": ["Businesses"],
            "allow_cross_track": True,
        },
    }
    tracks = [
        {
            "id": "businesses",
            "name": "Businesses",
            "entry_types": [{"name": "Business", "fields": []}],
        },
        {
            "id": "expenses",
            "name": "Expenses",
            "entry_types": [
                {
                    "name": "Expense",
                    "fields": [
                        {"key": "amount", "name": "Amount", "type": "number"},
                        relation,
                    ],
                }
            ],
        },
        {
            "id": "contacts",
            "name": "Contacts",
            "entry_types": [{"name": "Contact", "fields": []}],
        },
    ]
    seeds = [
        {"id": f"business.{i}", "track": "businesses", "title": title, "fields": {}}
        for i, title in enumerate(companies)
    ]
    seeds += [
        {
            "id": f"expense.{i}",
            "track": "expenses",
            "title": title,
            "fields": {
                "amount": amount,
                **({"business": business} if business else {}),
            },
        }
        for i, (title, amount, business) in enumerate(expenses)
    ]
    seeds += [
        {"id": f"contact.{i}", "track": "contacts", "title": title, "fields": {}}
        for i, title in enumerate(
            ["John Smith", "Sarah Johnson", "ABC Construction", "XYZ Supplies"]
        )
    ]
    blueprint, error = validate_blueprint(
        {
            "app": {"id": "app.personal", "name": "Personal Life Manager"},
            "tracks": tracks,
            "seeds": seeds,
        }
    )
    assert error is None
    plan = [{"tool": "integral_create_app", "args": {"name": blueprint["app"]["name"]}}]
    plan += [
        {
            "tool": "integral_create_app_track",
            "args": {
                "app_id": "{{app.id}}",
                "name": t["name"],
                "entry_types": deepcopy(t["entry_types"]),
            },
        }
        for t in blueprint["tracks"]
    ]
    names = {t["id"]: t["name"] for t in blueprint["tracks"]}
    plan += [
        {
            "tool": "integral_create_entry",
            "args": {
                "track_id": "{{track.id:" + names[s["track"]] + "}}",
                "title": s["title"],
                "fields": deepcopy(s["fields"]),
            },
        }
        for s in blueprint["seeds"]
    ]
    return blueprint, plan


def compiled(blueprint, plan):
    specs = {
        "{{track.id:"
        + t["name"]
        + "}}": [f for et in t["entry_types"] for f in et["fields"]]
        for t in blueprint["tracks"]
    }
    titles = {s["title"].casefold(): s["title"] for s in blueprint["seeds"]}
    return [
        (
            p["tool"],
            (
                scaffold_build._structured_seed(p["args"], specs, titles)
                if p["tool"] == "integral_create_entry"
                else p["args"]
            ),
        )
        for p in plan
    ]


def test_fifteen_seed_scenario_preserves_approved_values():
    blueprint, plan = scenario()
    before = deepcopy(blueprint)
    operations = compiled(blueprint, plan)
    assert plan_fidelity_errors(blueprint, operations, new_app=True) == []
    seeds = [args for tool, args in operations if tool == "integral_create_entry"]
    assert len(seeds) == 15
    linked = [s for s in seeds if "business" in s["fields"]]
    assert len(linked) == 5
    assert all(s["fields"]["business"].startswith("{{entry.id:") for s in linked)
    assert next(s for s in seeds if s["title"] == "coffee purchase")["fields"] == {
        "amount": 8
    }
    assert blueprint == before


@pytest.mark.parametrize(
    "changed",
    [
        {"business": "{{entry.id:Shell}}", "amount": 120},
        {"business": "{{entry.id:Unknown}}", "amount": 120},
        {"business": "{{entry.id:Walmart}}", "amount": 121},
    ],
)
def test_changed_relation_or_amount_still_fails(changed):
    blueprint, plan = scenario()
    operations = compiled(blueprint, plan)
    next(args for _, args in operations if args.get("title") == "groceries at Walmart")[
        "fields"
    ] = changed
    assert any(
        "groceries at Walmart" in e and "Mismatched:" in e
        for e in plan_fidelity_errors(blueprint, operations, new_app=True)
    )


def test_text_fields_cannot_become_references():
    blueprint, plan = scenario()
    field = blueprint["tracks"][1]["entry_types"][0]["fields"][1]
    field["type"] = "text"
    field.pop("relation")
    operations = compiled(blueprint, plan)
    next(args for _, args in operations if args.get("title") == "groceries at Walmart")[
        "fields"
    ]["business"] = "{{entry.id:Walmart}}"
    assert any(
        "Mismatched: business" in e
        for e in plan_fidelity_errors(blueprint, operations, new_app=True)
    )


def test_ambiguous_titles_cannot_authorize_a_reference():
    blueprint, plan = scenario()
    blueprint["seeds"].append(
        {"id": "contact.walmart", "track": "contacts", "title": "Walmart", "fields": {}}
    )
    assert any(
        "groceries at Walmart" in e and "Mismatched: business" in e
        for e in plan_fidelity_errors(
            blueprint, compiled(blueprint, plan), new_app=True
        )
    )


@pytest.mark.asyncio
async def test_full_approved_plan_reaches_commit(monkeypatch):
    from app.agentive.tooling import dispatch

    blueprint, plan = scenario()
    thread = SimpleNamespace(
        user_id="user-1",
        design_proposed={
            "approved": True,
            "summary": "Personal Life Manager",
            "proposal": "Personal Life Manager with Businesses, Expenses, and Contacts and the requested 15 entries.",
            "blueprint": blueprint,
            "blueprint_revision": 1,
        },
    )
    staged, commits = [], []

    async def affirmed(_):
        return True

    async def get_thread(_):
        return thread

    async def stage(tool, args, **_):
        staged.append((tool, args))
        return ToolResult(data={"batched": True})

    async def commit(name, args, **_):
        commits.append(name)
        return ToolResult(
            data={
                "_kind": "batch_applied",
                "applied": True,
                "token": "batch-1",
                "execute_result": {"completed": 19, "total": 19},
            }
        )

    monkeypatch.setattr(scaffold_build, "design_chat_affirmed_for_build", affirmed)
    monkeypatch.setattr(scaffold_build, "get_thread_by_session", get_thread)
    monkeypatch.setattr(dispatch, "dispatch_tool", stage)
    monkeypatch.setattr(dispatch, "_dispatch_batch_control", commit)
    _reset_for_tests()
    try:
        result = await scaffold_build.build_approved_design(
            {"operations": plan},
            principal_id="user-1",
            scope="ws-1",
            session_id="s-1",
            interaction_id="i-1",
        )
        assert not result.is_error, result.message
        assert len(staged) == 19
        assert commits == ["integral_commit_batch"]
    finally:
        _reset_for_tests()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", ["new", "before_rename", "already_renamed"])
async def test_actual_batch_build_and_graph_readback(test_user, legacy):
    """Real staging/executor/permission/readback with a trusted approval fixture."""
    from app.models.edges import CONTAINS, REFERENCES
    from app.models.nodes import Track
    from app.services import chat_threads
    from app.services.build_verification import verify_build
    from app.services.design_blueprint import blueprint_digest
    from app.services.personal_workspace import ensure_personal_workspace

    blueprint, plan = scenario()
    workspace = await ensure_personal_workspace(test_user)
    principal = test_user.user_id
    thread = await chat_threads.create_thread(
        user_id=principal,
        workspace_id=workspace.id,
        provider_id="integral_native",
        title="Approved relation smoke",
    )
    session = "approved-relation-smoke-" + thread.id
    await chat_threads.update_provider_session(thread, session)
    thread.design_proposed = {
        "approved": True,
        "proposed_at_user_turn": 1,
        "summary": "Personal Life Manager",
        "proposal": "Personal Life Manager with Businesses, Expenses, Contacts and 15 requested entries.",
        "design_id": "design.personal",
        "blueprint_revision": 1,
        "blueprint_digest": blueprint_digest(blueprint),
        "blueprint": blueprint,
    }
    await thread.save()
    await chat_threads.append_message(
        thread=thread,
        role="user",
        parts=[{"type": "text", "text": "Create Personal Life Manager"}],
    )
    await chat_threads.append_message(
        thread=thread,
        role="user",
        parts=[{"type": "text", "text": "Approved, build it"}],
    )
    _reset_for_tests()
    try:
        result = await scaffold_build.build_approved_design(
            {"operations": plan},
            principal_id=principal,
            scope=workspace.id,
            session_id=session,
            interaction_id="smoke-build",
        )
        assert not result.is_error, (result.error_code, result.message, result.data)
        assert result.data["completed"] == 19
        tracks = await Track.find({"context.workspace_id": workspace.id})
        entries = []
        for track in tracks:
            if track.title not in {"Businesses", "Expenses", "Contacts"}:
                continue
            entries.extend(
                await track.nodes(
                    edge=[CONTAINS], node=["Entry"], direction="out", limit=100
                )
            )
        assert len(entries) == 15
        by_title = {entry.title: entry for entry in entries}
        assert set(s["title"] for s in blueprint["seeds"]) <= set(by_title)
        for seed in blueprint["seeds"]:
            entry = by_title[seed["title"]]
            if seed["track"] == "expenses":
                assert entry.custom_fields["amount"] == seed["fields"]["amount"]
                business = seed["fields"].get("business")
                if business:
                    assert entry.custom_fields["business"] == by_title[business].id
                    context = await entry.get_context()
                    links = await context.find_edges_between(
                        entry.id, by_title[business].id, edge_class=REFERENCES
                    )
                    assert any(link.field_key == "business" for link in links)
                else:
                    assert not entry.custom_fields.get("business")
        tracks = await Track.find({"context.workspace_id": workspace.id})
        assert {"Businesses", "Expenses", "Contacts"} <= {t.title for t in tracks}
        verified = await verify_build(
            user_id=principal,
            workspace_id=workspace.id,
            design_id=result.data["design_id"],
            design_revision=result.data["design_revision"],
            execution_receipt_id=result.data["execution_receipt_id"],
        )
        assert verified["status"] == "verified", verified

        # Prompt 2: stage six exact changes before accepting any of them. Track
        # display renames must not invalidate the existing Expense relation.
        from app.agentive.services.staging_apply import bless_and_execute
        from app.agentive.staging import get_token
        from app.agentive.tooling.dispatch import dispatch_tool
        from app.models.nodes import App, Entry

        apps = await App.find({"context.workspace_id": workspace.id})
        app = next(a for a in apps if a.name == "Personal Life Manager")
        tracks_by_title = {t.title: t for t in tracks}
        if legacy != "new":
            business_track = tracks_by_title["Businesses"]
            business_track.relation_type_key = ""
            if legacy == "already_renamed":
                business_track.title = "Business Directory"
            await business_track.save()
        before_track_ids = {t.id for t in tracks}
        before_entry_ids = {e.id for e in entries}
        changes = [
            (
                "integral_update_app",
                {"app_id": app.id, "name": "Personal & Business Manager"},
            ),
            (
                "integral_update_track",
                {
                    "track_id": tracks_by_title["Businesses"].id,
                    "updates": {"title": "Business Directory"},
                },
            ),
            (
                "integral_update_track",
                {
                    "track_id": tracks_by_title["Contacts"].id,
                    "updates": {"title": "People & Organizations"},
                },
            ),
            (
                "integral_update_entry",
                {
                    "entry_id": by_title["Walmart"].id,
                    "updates": {"title": "Walmart Supercenter"},
                },
            ),
            (
                "integral_update_entry",
                {
                    "entry_id": by_title["groceries at Walmart"].id,
                    "updates": {"fields": {"amount": 150}},
                },
            ),
            (
                "integral_update_entry",
                {
                    "entry_id": by_title["John Smith"].id,
                    "updates": {"title": "John Smith — Project Manager"},
                },
            ),
        ]
        tokens = []
        for name, args in changes:
            staged = await dispatch_tool(
                name,
                args,
                principal_id=principal,
                scope=workspace.id,
                session_id=session,
                interaction_id="prompt-2",
            )
            assert not staged.is_error, (staged.error_code, staged.message)
            tokens.append(staged.data["token"])
        apply_order = tokens
        if legacy == "already_renamed":
            # Retry the Expense first, before another metadata write can
            # persist the recovered identity on the legacy target Track.
            apply_order = [tokens[i] for i in [0, 4, 1, 2, 3, 5]]
        for token in apply_order:
            applied = await bless_and_execute(user_id=principal, token=token)
            assert applied["consumed"], applied
            assert (await get_token(token)).state == "consumed"
            assert (await get_token(token)).last_error is None
        assert (await App.get(app.id)).name == "Personal & Business Manager"
        assert (
            await Track.get(tracks_by_title["Businesses"].id)
        ).title == "Business Directory"
        assert (
            await Track.get(tracks_by_title["Contacts"].id)
        ).title == "People & Organizations"
        assert (await Track.get(tracks_by_title["Expenses"].id)).title == "Expenses"
        assert (await Entry.get(by_title["Walmart"].id)).title == "Walmart Supercenter"
        expense = await Entry.get(by_title["groceries at Walmart"].id)
        assert expense.custom_fields["amount"] == 150
        assert expense.custom_fields["business"] == by_title["Walmart"].id
        assert (
            await Entry.get(by_title["John Smith"].id)
        ).title == "John Smith — Project Manager"
        assert {
            t.id for t in await Track.find({"context.workspace_id": workspace.id})
        } == before_track_ids
        after_entries = []
        for track in await Track.find({"context.workspace_id": workspace.id}):
            after_entries.extend(
                await track.nodes(edge=[CONTAINS], node=["Entry"], limit=100)
            )
        assert {e.id for e in after_entries} == before_entry_ids
        assert len(await App.find({"context.workspace_id": workspace.id})) == len(apps)

        # A genuine type mismatch still refuses the write. Approval is retained
        # separately from application, and an explicit retry uses that same
        # proposal after the target is compatible again.
        target = await Track.get(tracks_by_title["Businesses"].id)
        target.template_id = "incompatible_type"
        await target.save()
        staged = await dispatch_tool(
            "integral_update_entry",
            {"entry_id": expense.id, "updates": {"fields": {"amount": 150}}},
            principal_id=principal,
            scope=workspace.id,
            session_id=session,
            interaction_id="failure-check",
        )
        assert not staged.is_error, staged.message
        token = staged.data["token"]
        refused = await bless_and_execute(user_id=principal, token=token)
        assert refused["consumed"] is False
        failed = await get_token(token)
        assert failed.state == "blessed" and not failed.executing
        assert "target track type is not allowed" in failed.last_error["message"]
        assert (await Entry.get(expense.id)).custom_fields == expense.custom_fields
        target.template_id = None
        await target.save()
        from app.agentive.services.staging_apply import execute_blessed_change

        retried = await execute_blessed_change(user_id=principal, token=token)
        assert retried["consumed"] is True, retried
        assert (await get_token(token)).last_error is None
    finally:
        _reset_for_tests()
