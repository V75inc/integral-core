"""A reviewed sandbox attachment cannot silently change before execution."""

from types import SimpleNamespace

import pytest

from app.agentive import staging_executors
from app.agentive.tooling import bindings
from app.services import attachment_agent, policy_engine


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Provide an isolated caller sandbox."""
    monkeypatch.setenv("INTEGRAL_SANDBOX_ROOT", str(tmp_path))
    owner = tmp_path / "user-1"
    owner.mkdir()
    (owner / "report.md").write_text("Original report", encoding="utf-8")
    return owner


@pytest.mark.asyncio
async def test_review_binds_bytes_and_changed_source_is_refused(sandbox, monkeypatch):
    """A later file change cannot pass the reviewed digest."""

    async def load_entry(entry_id):
        return None

    monkeypatch.setattr(bindings._sd, "load_entry_record", load_entry)
    principal = bindings._propose_principal.set("user-1")
    try:
        proposal = await bindings._stage_attach_file(
            {"entry_id": "entry-1", "sandbox_path": "report.md"}
        )
    finally:
        bindings._propose_principal.reset(principal)
    assert len(proposal["payload"]["expected_sha256"]) == 64
    assert "15 bytes" in proposal["diff_human"]

    async def get_entry(entry_id):
        return SimpleNamespace(id=entry_id)

    async def allow(**kwargs):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(attachment_agent.Entry, "get", get_entry)
    monkeypatch.setattr(policy_engine, "evaluate", allow)
    (sandbox / "report.md").write_text("Changed report", encoding="utf-8")
    result = await staging_executors._x_attach_file("user-1", proposal["payload"])
    assert result["error_code"] == "sandbox_file_changed"


@pytest.mark.asyncio
async def test_legacy_review_without_digest_cannot_apply(sandbox, monkeypatch):
    """Old cards without an identity for their bytes must be reviewed anew."""

    async def get_entry(entry_id):
        return SimpleNamespace(id=entry_id)

    async def allow(**kwargs):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(attachment_agent.Entry, "get", get_entry)
    monkeypatch.setattr(policy_engine, "evaluate", allow)
    result = await attachment_agent.attach_sandbox_file_to_entry(
        user_id="user-1", entry_id="entry-1", sandbox_path="report.md"
    )
    assert result["error_code"] == "file_review_required"


@pytest.mark.asyncio
async def test_sandbox_does_not_follow_symlinks(sandbox, tmp_path):
    """Neither symlinks nor parent traversal escape the caller directory."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (sandbox / "redirect").symlink_to(outside, target_is_directory=True)
    (sandbox / "link.txt").symlink_to(outside / "secret.txt")
    for path in ("redirect/secret.txt", "link.txt", "../outside/secret.txt"):
        with pytest.raises(ValueError):
            await attachment_agent.inspect_sandbox_file("user-1", path)


@pytest.mark.asyncio
async def test_unchanged_review_attaches_exact_bytes(sandbox, monkeypatch):
    """Unchanged reviewed bytes become the entry attachment."""
    from app.models.edges import CONTAINS, HAS_ATTACHMENT
    from app.models.nodes import Attachment, Entry, Track
    from app.services import attachment_storage

    class Storage:
        content = None

        async def save_attachment(self, **kwargs):
            self.content = kwargs["content"]
            return {"path": "test/stored"}

    storage = Storage()

    async def allow(**kwargs):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(policy_engine, "evaluate", allow)
    monkeypatch.setattr(
        attachment_storage, "get_attachment_storage_service", lambda: storage
    )
    track = await Track.create(title="Reports", visibility="private")
    entry = await Entry.create(title="Research", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    reviewed = await attachment_agent.inspect_sandbox_file("user-1", "report.md")
    result = await attachment_agent.attach_sandbox_file_to_entry(
        user_id="user-1",
        entry_id=entry.id,
        sandbox_path="report.md",
        expected_sha256=reviewed["sha256"],
    )
    assert not result.get("error")
    assert storage.content == b"Original report"
    linked = await entry.nodes(
        edge=[HAS_ATTACHMENT], node=[Attachment], direction="out"
    )
    assert [item.id for item in linked] == [result["attachment"]["id"]]


@pytest.mark.asyncio
async def test_sandbox_size_limit_is_enforced(sandbox, monkeypatch):
    """Sandbox files respect the configured attachment limit."""
    monkeypatch.setattr(attachment_agent.settings, "ATTACHMENT_MAX_UPLOAD_BYTES", 4)
    with pytest.raises(ValueError, match="size limit"):
        await attachment_agent.inspect_sandbox_file("user-1", "report.md")


@pytest.mark.asyncio
async def test_review_preview_is_scoped_bounded_and_truthful(sandbox):
    """Text previews expose exactly a bounded prefix of the hashed bytes."""
    small = await attachment_agent.inspect_sandbox_file("user-1", "report.md")
    assert small["preview"] == {
        "available": True,
        "text": "Original report",
        "complete": True,
    }
    (sandbox / "long.txt").write_text("x" * 12_001, encoding="utf-8")
    long = await attachment_agent.inspect_sandbox_file("user-1", "long.txt")
    assert long["preview"]["complete"] is False
    assert len(long["preview"]["text"]) == 12_000
    (sandbox / "binary.txt").write_bytes(b"text\x00binary")
    binary = await attachment_agent.inspect_sandbox_file("user-1", "binary.txt")
    assert binary["preview"] == {"available": False}
    (sandbox / "report.pdf").write_bytes(b"%PDF-1.7")
    pdf = await attachment_agent.inspect_sandbox_file("user-1", "report.pdf")
    assert pdf["preview"] == {"available": False}
