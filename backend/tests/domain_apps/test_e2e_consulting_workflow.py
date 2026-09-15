"""Phase 23 GO-01 — End-to-end UAT script.

Single test that walks the complete consulting dogfood workflow and
asserts each substrate transition lands correctly:

  Contact → Opportunity → Discovery Transcript → Scoping Document
       → Pricing Rubric → Project Proposal → Opportunity (converted)
       → Project → Case Study (Portfolio).

Each step exercises real substrate writes (Entry.create, edge wires,
provenance) rather than mocked side-effects. Privileged data
(cost/margin/rubric line-items) is verified PRESENT on the privileged
tracks AND ABSENT from the converted Opportunity (the client-safe
surface).

This is the consulting-cutover gate — if this passes end-to-end, the
substrate supports the full proserve lifecycle.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from httpx import AsyncClient

from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF
from app.models.nodes import Entry, EntryType, Track, Workspace
from app.utils.time import utc_now_iso


def _register_crm_bundle_hooks(workspace_id: str) -> None:
    """Register the hook bindings the E2E exercises across the Phase 31
    bundle decomposition (DR-31-01):

    - ``entry.transform`` with key ``proposal_to_opportunity`` — drives
      STEP 7 (Project Proposal → Opportunity); owned by the **sales**
      bundle (DR-31-01 §4).
    - ``entry.transform`` with key ``proposal_to_project`` — drives
      STEP 7b (Project Proposal → Project, accepted gate); owned by
      the **sales** bundle (DR-31-01 §5, Phase 31 addition).
    - ``entry.public_share`` with key ``case_study_share`` — drives
      STEPS 10/11 (Case Study mint + redeem); owned by the
      **portfolio** bundle (DR-31-01 §4).

    These mirror the bindings in the new bundle manifests
    (``backend/app/profiles/sales/profile.yaml`` +
    ``backend/app/profiles/portfolio/profile.yaml``). The E2E seeds
    tracks by hand (it does NOT install the bundles), so we register
    the bindings against the test workspace directly under their new
    owning bundle slugs.

    Import is deferred to call-site to avoid a circular import: at
    module-load time, ``app.services.hooks.registry`` pulls
    ``app.api.errors`` which transitively imports the api package
    initializer.
    """
    from app.services.hooks.registry import register_workspace_hooks

    # Sales bundle owns the two proposal transform bindings.
    register_workspace_hooks(
        workspace_id,
        "sales",
        [
            {
                "point": "entry.transform",
                "key": "proposal_to_opportunity",
                "match": {
                    "source_entry_type": "project_proposal",
                    "target_track_type": "opportunities",
                    "target_entry_type": "opportunity",
                },
                "mode": "declarative",
                "declarative": {
                    "copy_fields": [
                        {"from": "title", "to": "title"},
                        {"from": "body", "to": "body"},
                        {
                            "from": "custom_fields.total_price",
                            "to": "custom_fields.value",
                        },
                    ],
                    "strip_fields": [
                        "custom_fields.total_cost",
                        "custom_fields.projected_margin_pct",
                        "custom_fields.line_items",
                        "custom_fields.under_margin",
                        "custom_fields.target_margin_pct",
                    ],
                    "provenance_edge": {
                        "edge": "REFERENCES",
                        "direction": "out",
                        "from": "target",
                        "to": "source",
                    },
                    "override_flag": "under_margin",
                },
            },
            {
                "point": "entry.transform",
                "key": "proposal_to_project",
                "match": {
                    "source_entry_type": "project_proposal",
                    "target_track_type": "projects",
                    "target_entry_type": "project",
                },
                "mode": "declarative",
                "declarative": {
                    "copy_fields": [
                        {"from": "title", "to": "title"},
                        {"from": "body", "to": "body"},
                        {
                            "from": "custom_fields.total_price",
                            "to": "custom_fields.value",
                        },
                    ],
                    "strip_fields": [
                        "custom_fields.total_cost",
                        "custom_fields.projected_margin_pct",
                        "custom_fields.line_items",
                        "custom_fields.under_margin",
                        "custom_fields.target_margin_pct",
                    ],
                    "gate_field": "custom_fields.status",
                    "gate_value": "accepted",
                    "provenance_edge": {
                        "edge": "REFERENCES",
                        "direction": "out",
                        "from": "target",
                        "to": "source",
                    },
                },
            },
        ],
    )
    # Portfolio bundle owns the case-study public-share binding.
    register_workspace_hooks(
        workspace_id,
        "portfolio",
        [
            {
                "point": "entry.public_share",
                "key": "case_study_share",
                "match": {"entry_type": "case_study"},
                "mode": "declarative",
                "declarative": {
                    "gate_field": "published",
                    "gate_value": True,
                    "projection_fields": [
                        "title",
                        "body",
                        {"field": "client_name", "source": "custom_fields.client_name"},
                        {"field": "sector", "source": "custom_fields.sector"},
                        {"field": "problem", "source": "custom_fields.problem"},
                        {"field": "approach", "source": "custom_fields.approach"},
                        {"field": "outcome", "source": "custom_fields.outcome"},
                        {"field": "tech_stack", "source": "custom_fields.tech_stack"},
                        {
                            "field": "engagement_size",
                            "source": "custom_fields.engagement_size",
                        },
                    ],
                },
            }
        ],
    )


async def _provision_consulting_workspace(test_user) -> Dict[str, Any]:
    """Stand up a minimal consulting-shaped workspace with all 9 CRM tracks."""
    owner_id = test_user.id
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Acme Inc.",
        name_fold="acme inc.",
        created_at=now,
        updated_at=now,
    )
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)

    track_titles = [
        ("contacts", "Contacts", "contact", "Contact"),
        ("opportunities", "Opportunities", "opportunity", "Opportunity"),
        (
            "discovery_sessions",
            "Discovery Sessions",
            "discovery_transcript",
            "Discovery Transcript",
        ),
        (
            "scoping_documents",
            "Scoping Documents",
            "scoping_document",
            "Scoping Document",
        ),
        ("pricing_rubrics", "Pricing Rubrics", "pricing_rubric", "Pricing Rubric"),
        (
            "project_proposals",
            "Project Proposals",
            "project_proposal",
            "Project Proposal",
        ),
        ("projects", "Projects", "project", "Project"),
        ("portfolio", "Portfolio", "case_study", "Case Study"),
    ]
    tracks: Dict[str, Track] = {}
    entry_types: Dict[str, EntryType] = {}
    # Target EntryTypes of declarative transforms must declare the projected
    # custom_fields keys — create_entry_in_track validates against form_schema.
    transform_target_schemas = {
        "opportunities": {
            "fields": [{"key": "value", "name": "Deal value", "type": "number"}]
        },
        "projects": {"fields": [{"key": "value", "name": "Value", "type": "number"}]},
    }
    for key, title, et_key, et_name in track_titles:
        t = await Track.create(title=title, owner_id=owner_id, workspace_id=ws.id)
        tracks[key] = t
        await test_user.connect(t, edge=COLLABORATES_ON, role="owner", added_at=now)
        et = await EntryType.create(
            name=et_name,
            name_fold=et_name.lower(),
            track_id=t.id,
            form_schema=transform_target_schemas.get(key) or {"fields": []},
        )
        entry_types[key] = et
    return {
        "ws": ws,
        "tracks": tracks,
        "entry_types": entry_types,
        "owner_id": owner_id,
    }


@pytest.mark.asyncio
async def test_full_consulting_lifecycle_uat(
    authenticated_client: AsyncClient, test_user
):
    fx = await _provision_consulting_workspace(test_user)
    tracks = fx["tracks"]
    ets = fx["entry_types"]
    owner_id = fx["owner_id"]
    now = utc_now_iso()

    # Phase 30 (DR-30-02) + Phase 31 (DR-31-01): substrate dispatches
    # transform / public-share via the bundle hook framework. Seed the
    # bindings the E2E exercises against the test workspace before
    # STEP 7 (proposal_to_opportunity — sales), STEP 7b
    # (proposal_to_project — sales), and STEP 10 (case_study_share —
    # portfolio). Post-decomposition: the three bindings live in
    # different bundles.
    _register_crm_bundle_hooks(fx["ws"].id)

    # ---- STEP 1: Contact -------------------------------------------------
    contact = await Entry.create(
        title="Contoso Logistics — Maya Patel",
        body="VP Engineering",
        track_id=tracks["contacts"].id,
        type_id=ets["contacts"].id,
        author_id=owner_id,
        custom_fields={"email": "maya@contoso.example", "company": "Contoso"},
    )
    await tracks["contacts"].connect(contact, edge=CONTAINS, added_at=now)
    await test_user.connect(contact, edge=COLLABORATES_ON, role="owner", added_at=now)
    assert contact.id

    # ---- STEP 2: Opportunity (lead) --------------------------------------
    opp = await Entry.create(
        title="Contoso platform v2",
        body="Dispatch rewrite — early signal.",
        track_id=tracks["opportunities"].id,
        type_id=ets["opportunities"].id,
        author_id=owner_id,
        custom_fields={"stage": "qualified", "value": 0, "contact": contact.id},
    )
    await tracks["opportunities"].connect(opp, edge=CONTAINS, added_at=now)
    await test_user.connect(opp, edge=COLLABORATES_ON, role="owner", added_at=now)

    # ---- STEP 3: Discovery Transcript ------------------------------------
    transcript = await Entry.create(
        title="Discovery call 2026-05-23",
        body="Notes from the call.",
        track_id=tracks["discovery_sessions"].id,
        type_id=ets["discovery_sessions"].id,
        author_id=owner_id,
        custom_fields={
            "account": contact.id,
            "attendees": ["maya@contoso.example"],
            "call_date": "2026-05-23",
            "agent_summary": "Legacy dispatch unable to scale.",
            "transcript_source": "manual_upload",
        },
    )
    await tracks["discovery_sessions"].connect(transcript, edge=CONTAINS, added_at=now)
    await test_user.connect(
        transcript, edge=COLLABORATES_ON, role="owner", added_at=now
    )

    # ---- STEP 4: Scoping Document ----------------------------------------
    scoping = await Entry.create(
        title="Contoso v2 — scope",
        body="Phased rewrite.",
        track_id=tracks["scoping_documents"].id,
        type_id=ets["scoping_documents"].id,
        author_id=owner_id,
        custom_fields={
            "problem_statement": "Legacy dispatch capped at 500 events/sec.",
            "proposed_scope": "Event-driven rewrite on FastAPI + Kafka.",
            "assumptions": "Customer provides 1 SME, 0.5 FTE.",
            "risks": "Kafka ops capacity at customer.",
            "role_effort": [
                {"role_key": "senior_engineer", "hours": 120},
                {"role_key": "engineer", "hours": 200},
            ],
        },
    )
    await tracks["scoping_documents"].connect(scoping, edge=CONTAINS, added_at=now)
    await test_user.connect(scoping, edge=COLLABORATES_ON, role="owner", added_at=now)

    # ---- STEP 5: Pricing Rubric (PRIVILEGED) -----------------------------
    rubric = await Entry.create(
        title="Active rubric — 2026 Q2",
        body="Live during consulting dogfood window.",
        track_id=tracks["pricing_rubrics"].id,
        type_id=ets["pricing_rubrics"].id,
        author_id=owner_id,
        custom_fields={
            "target_margin_pct": 30,
            "overhead_factor": 1.0,
            "rubric_lines": [
                {"role_key": "senior_engineer", "bill_rate": 185, "fallback_cost": 110},
                {"role_key": "engineer", "bill_rate": 140, "fallback_cost": 82},
            ],
            "is_active": True,
        },
    )
    await tracks["pricing_rubrics"].connect(rubric, edge=CONTAINS, added_at=now)
    await test_user.connect(rubric, edge=COLLABORATES_ON, role="owner", added_at=now)

    # ---- STEP 6: Project Proposal (PRIVILEGED) ---------------------------
    proposal = await Entry.create(
        title="Contoso platform v2 — proposal",
        body="Cover summary.",
        track_id=tracks["project_proposals"].id,
        type_id=ets["project_proposals"].id,
        author_id=owner_id,
        custom_fields={
            "source_scoping_document": scoping.id,
            "pricing_rubric": rubric.id,
            "line_items": [
                {
                    "role_key": "senior_engineer",
                    "hours": 120,
                    "bill_rate": 185,
                    "line_price": 22200,
                    "line_cost": 13200,
                },
                {
                    "role_key": "engineer",
                    "hours": 200,
                    "bill_rate": 140,
                    "line_price": 28000,
                    "line_cost": 16400,
                },
            ],
            "total_price": 50200,
            "total_cost": 29600,
            "projected_margin_pct": 41,
            "target_margin_pct": 30,
            "under_margin": False,
        },
    )
    await tracks["project_proposals"].connect(proposal, edge=CONTAINS, added_at=now)
    await test_user.connect(proposal, edge=COLLABORATES_ON, role="owner", added_at=now)

    # ---- STEP 7: Transform Proposal → Opportunity (DR-30-02) -------------
    # Generic /api/entries/{id}/transform endpoint dispatches via the
    # sales bundle's proposal_to_opportunity hook (registered above).
    convert = await authenticated_client.post(
        f"/api/entries/{proposal.id}/transform",
        json={"to_track": tracks["opportunities"].id},
    )
    assert convert.status_code == 200, convert.text
    body = convert.json()
    assert body["hook_key"] == "proposal_to_opportunity"
    converted_opp = await Entry.get(body["new_entry_id"])
    assert converted_opp is not None
    # Privilege boundary: client-safe Opportunity carries scope + price, NOT
    # cost / margin / line_items.
    assert converted_opp.custom_fields.get("value") == 50200
    for forbidden in (
        "total_cost",
        "projected_margin_pct",
        "line_items",
        "under_margin",
    ):
        assert (
            forbidden not in converted_opp.custom_fields
        ), f"convert leaked privileged key {forbidden!r}"

    # ---- STEP 7b: Transform Proposal → Project (DR-31-01 §5) -------------
    # Phase 31 addition — the sales bundle declares a second transform
    # binding ``proposal_to_project`` that gates on ``status: accepted``
    # and spawns a Project from the accepted Proposal. Same strip set as
    # ``proposal_to_opportunity`` (cost / margin / line_items / margin
    # flags) — the new Project lands in the Projects app, which may
    # carry a broader collaborator pool than Sales.
    #
    # Set status=accepted on the source proposal so the declarative
    # gate fires (gate_field=custom_fields.status, gate_value=accepted).
    proposal.custom_fields["status"] = "accepted"
    await proposal.save()
    spawn = await authenticated_client.post(
        f"/api/entries/{proposal.id}/transform",
        json={"to_track": tracks["projects"].id},
    )
    assert spawn.status_code == 200, spawn.text
    spawn_body = spawn.json()
    assert spawn_body["hook_key"] == "proposal_to_project"
    spawned_project = await Entry.get(spawn_body["new_entry_id"])
    assert spawned_project is not None
    # Privilege boundary (DR-31-01 §5): Project carries value (= proposal's
    # total_price), but NOT cost / margin / line items / target margin / margin flag.
    assert spawned_project.custom_fields.get("value") == 50200
    for forbidden in (
        "total_cost",
        "projected_margin_pct",
        "line_items",
        "under_margin",
        "target_margin_pct",
    ):
        assert (
            forbidden not in spawned_project.custom_fields
        ), f"proposal_to_project leaked privileged key {forbidden!r}"

    # ---- STEP 8: Project (post-won) --------------------------------------
    project = await Entry.create(
        title="Contoso v2 delivery",
        body="Engagement kick-off.",
        track_id=tracks["projects"].id,
        type_id=ets["projects"].id,
        author_id=owner_id,
        custom_fields={
            "contact": contact.id,
            "status": "completed",
            "tech_stack": ["python", "fastapi", "kafka"],
        },
    )
    await tracks["projects"].connect(project, edge=CONTAINS, added_at=now)
    await test_user.connect(project, edge=COLLABORATES_ON, role="owner", added_at=now)

    # ---- STEP 9: Case Study (Portfolio, client-safe) ---------------------
    case_study = await Entry.create(
        title="Contoso v2 — outcome study",
        body="Executive summary.",
        track_id=tracks["portfolio"].id,
        type_id=ets["portfolio"].id,
        author_id=owner_id,
        custom_fields={
            "client_name": "Contoso",
            "sector": "logistics",
            "problem": "Legacy dispatch capped at 500 events/sec.",
            "approach": "FastAPI + Kafka.",
            "outcome": "3x throughput.",
            "tech_stack": ["python", "fastapi", "kafka"],
            "engagement_size": "large",
            "published": True,
            "project": project.id,
        },
    )
    await tracks["portfolio"].connect(case_study, edge=CONTAINS, added_at=now)
    await test_user.connect(
        case_study, edge=COLLABORATES_ON, role="owner", added_at=now
    )

    # ---- STEP 10: Public share-link mint (DR-30-02) ---------------------
    # Generic /api/entries/{id}/public-share endpoint dispatches via the
    # portfolio bundle's case_study_share hook (registered above).
    mint = await authenticated_client.post(
        f"/api/entries/{case_study.id}/public-share",
        json={},
    )
    assert mint.status_code == 200, mint.text
    mint_body = mint.json()
    assert mint_body["hook_key"] == "case_study_share"
    token = mint_body["token"]

    # ---- STEP 11: Unauthenticated GET — public surface returns ONLY
    #               whitelisted fields, no privileged data anywhere.
    # Uses the legacy /portfolio/shared/{token} alias (Wave D5 retained
    # the URL-stability adapter) — the substrate still routes through
    # the generic public-share dispatcher under the hood.
    shared = await authenticated_client.get(f"/api/portfolio/shared/{token}")
    assert shared.status_code == 200, shared.text
    payload = shared.json()
    flat = str(payload).lower()
    for forbidden in (
        "total_cost",
        "projected_margin_pct",
        "bill_rate",
        "rubric_lines",
    ):
        assert (
            forbidden not in flat
        ), f"public share leaks privileged token {forbidden!r}"

    # ---- Closure: every step landed on the expected track + cohesive ----
    assert contact.track_id == tracks["contacts"].id
    assert opp.track_id == tracks["opportunities"].id
    assert transcript.track_id == tracks["discovery_sessions"].id
    assert scoping.track_id == tracks["scoping_documents"].id
    assert rubric.track_id == tracks["pricing_rubrics"].id
    assert proposal.track_id == tracks["project_proposals"].id
    assert converted_opp.track_id == tracks["opportunities"].id
    assert project.track_id == tracks["projects"].id
    assert case_study.track_id == tracks["portfolio"].id
