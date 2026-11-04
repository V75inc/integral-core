"""Public Core mutation routes enforce submission ownership before effects."""

from unittest.mock import AsyncMock

import pytest

from app.models.nodes import Entry
from app.services.app_graph import catalog_track, catalog_workspace
from app.services.public_submission_capability import (
    PUBLIC_SUBMISSION_CAPABILITY_REQUIRED_FIELD,
    bind_public_submission_capability,
    issue_public_submission_capability,
)
from app.utils.time import utc_now_iso


async def _public_candidates(authenticated_client, test_user):
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.models.nodes import EntryType, Track, Workspace
    from app.services.app_graph import ensure_track_attached_operational_model

    now = utc_now_iso()
    workspace = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Capability Isolation WS",
        name_fold="capability isolation ws",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(workspace)
    await test_user.connect(workspace, edge=IS_MEMBER_OF, role="owner", added_at=now)
    track = await Track.create(
        title="Recruitment intake",
        owner_id=test_user.id,
        workspace_id=workspace.id,
        visibility="private",
    )
    await catalog_track(track)
    await test_user.connect(track, edge=COLLABORATES_ON, role="owner", added_at=now)
    entry_type = await EntryType.create(
        name="Candidate",
        name_fold="candidate",
        track_id=track.id,
        form_schema={
            "fields": [
                {
                    "key": "priority",
                    "type": "select",
                    "enum": ["high", "low"],
                    "required": True,
                },
                {"key": "resume", "type": "text", "required": False},
                {"key": "cover_letter", "type": "text", "required": False},
                {"key": "notes", "type": "text", "required": False},
            ]
        },
    )
    await ensure_track_attached_operational_model(track)
    response = await authenticated_client.post(
        f"/api/tracks/{track.id}/public-share",
        json={
            "enabled": True,
            "public_permissions": {
                "read_entries": True,
                "create_entries": True,
                "update_entries": True,
                "read_comments": True,
                "create_comments": True,
            },
        },
    )
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    auth = authenticated_client.headers.pop("Authorization", None)
    try:
        entries = []
        for name in ("Applicant A", "Applicant B"):
            created = await authenticated_client.post(
                f"/api/public-share/track/{token}/entries",
                json={
                    "title": name,
                    "type_id": entry_type.id,
                    "body": "Synthetic test record",
                    "custom_fields": {
                        "priority": "high",
                        "resume": f"old-{name}-resume",
                        "cover_letter": f"old-{name}-cover",
                        "notes": f"old-{name}-notes",
                    },
                },
            )
            assert created.status_code == 200, created.text
            entries.append(await Entry.get(created.json()["entry"]["id"]))
    finally:
        if auth:
            authenticated_client.headers["Authorization"] = auth

    from app.api.tracks_public_share import _resolve_public_track_link

    link = await _resolve_public_track_link(token)
    capabilities = []
    for label, entry in zip(("a", "b"), entries, strict=True):
        issued = await issue_public_submission_capability(
            workspace_id=workspace.id,
            track_id=track.id,
            form_id=entry_type.id,
            share_link_id=link.id,
            submission_id=f"pending-{label}",
        )
        assert await bind_public_submission_capability(
            issued["capability"],
            workspace_id=workspace.id,
            track_id=track.id,
            form_id=entry_type.id,
            share_link_id=link.id,
            submission_id=f"pending-{label}",
            entry_id=entry.id,
        )
        entry.custom_fields = {
            **entry.custom_fields,
            PUBLIC_SUBMISSION_CAPABILITY_REQUIRED_FIELD: True,
        }
        await entry.save()
        capabilities.append(issued["capability"])
    return (
        token,
        track,
        entry_type,
        entries[0],
        entries[1],
        capabilities[0],
        capabilities[1],
    )


@pytest.mark.asyncio
async def test_capability_protects_public_reads_comments_and_reactions(
    authenticated_client, test_user
):
    token, _track, _entry_type, _entry_a, entry_b, capability_a, capability_b = (
        await _public_candidates(authenticated_client, test_user)
    )
    auth = authenticated_client.headers.pop("Authorization", None)
    entry_url = f"/api/public-share/track/{token}/entries/{entry_b.id}"
    comments_url = f"{entry_url}/comments"
    reactions_url = f"{entry_url}/reactions"
    try:
        denied_requests = [
            ("get", entry_url, {}),
            ("get", comments_url, {}),
            ("post", comments_url, {"json": {"text": "Applicant A comment"}}),
            ("post", reactions_url, {"json": {"emoji": "👍"}}),
            ("delete", f"{reactions_url}/👍", {}),
        ]
        for method, url, kwargs in denied_requests:
            denied = await getattr(authenticated_client, method)(url, **kwargs)
            assert denied.status_code == 404, denied.text

        for method, url, kwargs in denied_requests:
            denied = await getattr(authenticated_client, method)(
                url, headers={"X-Entry-Capability": capability_a}, **kwargs
            )
            assert denied.status_code == 404, denied.text

        unchanged = await Entry.get(entry_b.id)
        assert not unchanged.reactions
        assert await unchanged.nodes(edge=["HAS_COMMENT"], node=["Comment"]) == []

        own_entry = await authenticated_client.get(
            entry_url, headers={"X-Entry-Capability": capability_b}
        )
        assert own_entry.status_code == 200, own_entry.text
        own_comments = await authenticated_client.get(
            comments_url, headers={"X-Entry-Capability": capability_b}
        )
        assert own_comments.status_code == 200, own_comments.text
        assert own_comments.json()["total"] == 0

        own_comment = await authenticated_client.post(
            comments_url,
            json={"text": "Applicant B comment"},
            headers={"X-Entry-Capability": capability_b},
        )
        assert own_comment.status_code == 200, own_comment.text
        own_reaction = await authenticated_client.post(
            reactions_url,
            json={"emoji": "👍"},
            headers={"X-Entry-Capability": capability_b},
        )
        assert own_reaction.status_code == 200, own_reaction.text
        remove_reaction = await authenticated_client.delete(
            f"{reactions_url}/👍",
            headers={"X-Entry-Capability": capability_b},
        )
        assert remove_reaction.status_code == 200, remove_reaction.text
    finally:
        if auth:
            authenticated_client.headers["Authorization"] = auth


@pytest.mark.asyncio
async def test_public_collections_never_enumerate_protected_submissions(
    authenticated_client, test_user
):
    token, _track, entry_type, entry_a, entry_b, capability_a, _capability_b = (
        await _public_candidates(authenticated_client, test_user)
    )
    entry_a.custom_fields.pop(PUBLIC_SUBMISSION_CAPABILITY_REQUIRED_FIELD)
    await entry_a.save()
    entry_type.form_schema["fields"].append(
        {"key": "related", "type": "relation", "relation": {"target": "entry"}}
    )
    await entry_type.save()
    auth = authenticated_client.headers.pop("Authorization", None)
    try:
        for headers in ({}, {"X-Entry-Capability": capability_a}):
            listed = await authenticated_client.get(
                f"/api/public-share/track/{token}/entries", headers=headers
            )
            assert listed.status_code == 200, listed.text
            assert [row["id"] for row in listed.json()["entries"]] == [entry_a.id]
            assert listed.json()["total"] == 1
            assert entry_b.id not in listed.text
            searched = await authenticated_client.get(
                f"/api/public-share/track/{token}/entries",
                params={"q": entry_b.title},
                headers=headers,
            )
            assert searched.status_code == 200, searched.text
            assert searched.json()["entries"] == []
            assert searched.json()["total"] == 0
            options = await authenticated_client.get(
                f"/api/public-share/track/{token}/relation-options",
                params={"entry_type_id": entry_type.id, "field_key": "related"},
                headers=headers,
            )
            assert options.status_code == 200, options.text
            assert [row["id"] for row in options.json()["targets"]] == [entry_a.id]
            assert entry_b.id not in options.text
    finally:
        if auth:
            authenticated_client.headers["Authorization"] = auth


@pytest.mark.asyncio
async def test_protected_apply_snapshot_requires_its_own_capability(
    authenticated_client, test_user, monkeypatch
):
    token, _track, _entry_type, _entry_a, entry_b, capability_a, capability_b = (
        await _public_candidates(authenticated_client, test_user)
    )
    monkeypatch.setattr(
        "app.api.tracks_public_share._entry_type_slug", lambda _: "opening"
    )
    auth = authenticated_client.headers.pop("Authorization", None)
    try:
        url = f"/api/public-share/track/{token}/apply?opening={entry_b.id}"
        for headers in ({}, {"X-Entry-Capability": capability_a}):
            denied = await authenticated_client.get(url, headers=headers)
            assert denied.status_code == 404, denied.text
        own = await authenticated_client.get(
            url, headers={"X-Entry-Capability": capability_b}
        )
        assert own.status_code == 200, own.text
        assert own.json()["opening"]["id"] == entry_b.id
    finally:
        if auth:
            authenticated_client.headers["Authorization"] = auth


@pytest.mark.asyncio
async def test_applicant_a_cannot_mutate_b_entry_or_replace_either_file(
    authenticated_client, test_user, monkeypatch
):
    token, _track, _entry_type, _entry_a, entry_b, capability_a, capability_b = (
        await _public_candidates(authenticated_client, test_user)
    )
    from app.api import attachments
    from app.services import email_service

    persist = AsyncMock(
        side_effect=[
            {"attachment": {"id": "n.Attachment.synthetic-resume-b"}},
            {"attachment": {"id": "n.Attachment.synthetic-cover-b"}},
        ]
    )
    send_email = AsyncMock()
    monkeypatch.setattr(attachments, "_persist_uploaded_file", persist)
    monkeypatch.setattr(email_service, "send_email", send_email)
    auth = authenticated_client.headers.pop("Authorization", None)
    try:
        for field in ("resume", "cover_letter"):
            denied = await authenticated_client.post(
                f"/api/public-share/track/{token}/entries/{entry_b.id}/attachments?field_key={field}",
                files={
                    "file": (
                        f"{field}.pdf",
                        b"synthetic applicant A",
                        "application/pdf",
                    )
                },
                headers={"X-Entry-Capability": capability_a},
            )
            assert denied.status_code == 404, denied.text
        missing_capability = await authenticated_client.post(
            f"/api/public-share/track/{token}/entries/{entry_b.id}/attachments?field_key=resume",
            files={"file": ("resume.pdf", b"synthetic applicant A", "application/pdf")},
        )
        assert missing_capability.status_code == 404, missing_capability.text
        invalid_capability = await authenticated_client.post(
            f"/api/public-share/track/{token}/entries/{entry_b.id}/attachments?field_key=resume",
            files={"file": ("resume.pdf", b"synthetic applicant A", "application/pdf")},
            headers={"X-Entry-Capability": "not-a-valid-capability"},
        )
        assert invalid_capability.status_code == 404, invalid_capability.text
        denied_update = await authenticated_client.patch(
            f"/api/public-share/track/{token}/entries/{entry_b.id}",
            json={"title": "changed by applicant A"},
            headers={"X-Entry-Capability": capability_a},
        )
        assert denied_update.status_code == 404, denied_update.text

        unchanged = await Entry.get(entry_b.id)
        assert unchanged.title == "Applicant B"
        assert unchanged.custom_fields["resume"] == "old-Applicant B-resume"
        assert unchanged.custom_fields["cover_letter"] == "old-Applicant B-cover"
        assert unchanged.custom_fields["notes"] == "old-Applicant B-notes"
        assert (
            unchanged.custom_fields[PUBLIC_SUBMISSION_CAPABILITY_REQUIRED_FIELD] is True
        )
        persist.assert_not_awaited()
        send_email.assert_not_awaited()

        for field in ("resume", "cover_letter"):
            owner_upload = await authenticated_client.post(
                f"/api/public-share/track/{token}/entries/{entry_b.id}/attachments?field_key={field}",
                files={
                    "file": (
                        f"{field}.pdf",
                        b"synthetic applicant B",
                        "application/pdf",
                    )
                },
                headers={"X-Entry-Capability": capability_b},
            )
            assert owner_upload.status_code == 200, owner_upload.text
        owner_update = await authenticated_client.patch(
            f"/api/public-share/track/{token}/entries/{entry_b.id}",
            json={"title": "Applicant B updated own record"},
            headers={"X-Entry-Capability": capability_b},
        )
        assert owner_update.status_code == 200, owner_update.text
        updated = await Entry.get(entry_b.id)
        assert updated.title == "Applicant B updated own record"
        assert updated.custom_fields["resume"] == "n.Attachment.synthetic-resume-b"
        assert updated.custom_fields["cover_letter"] == "n.Attachment.synthetic-cover-b"
        assert (
            updated.custom_fields[PUBLIC_SUBMISSION_CAPABILITY_REQUIRED_FIELD] is True
        )
        assert persist.await_count == 2
        send_email.assert_not_awaited()
    finally:
        if auth:
            authenticated_client.headers["Authorization"] = auth
