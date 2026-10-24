"""Expensive previews must not multiply with viewers or outlive cancelled work."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import attachment_preview as preview

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def attachment(key="attachment"):
    return SimpleNamespace(
        id=key,
        source_type="file",
        mime_type="application/msword",
        storage_key=key,
        preview_storage_key="",
        filename="document.doc",
        size=1,
        save=AsyncMock(),
    )


def storage():
    return SimpleNamespace(
        read_attachment=AsyncMock(return_value=b"original"),
        get_metadata=AsyncMock(return_value={"size": 3}),
        build_sibling_key=lambda *args: "/".join(args),
        _storage=SimpleNamespace(save_file=AsyncMock(return_value={})),
    )


@pytest.mark.asyncio
async def test_overlapping_previews_share_conversion_and_keep_graph_in_owner_task(
    monkeypatch,
):
    source = attachment()
    svc = storage()
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    owner_task = None

    async def convert(*args, **kwargs):
        assert asyncio.current_task() is owner_task
        calls.append(True)
        started.set()
        await release.wait()
        return b"pdf"

    monkeypatch.setattr(preview, "_resolve_libreoffice_binary", lambda: "office")
    monkeypatch.setattr(preview, "_convert_to_pdf", convert)
    owner_task = asyncio.create_task(
        preview.ensure_preview(source, entry_id="entry", storage=svc)
    )
    await started.wait()
    cancelled = asyncio.create_task(
        preview.ensure_preview(attachment(), entry_id="entry", storage=svc)
    )
    follower = asyncio.create_task(
        preview.ensure_preview(attachment(), entry_id="entry", storage=svc)
    )
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release.set()
    assert await owner_task == await follower
    assert calls == [True]
    source.save.assert_awaited_once()
    assert preview._PREVIEW_SLOTS.get(asyncio.get_running_loop()) is None


@pytest.mark.asyncio
async def test_cached_preview_checks_metadata_without_reading_pdf(monkeypatch):
    source = attachment()
    source.preview_storage_key = "cached.pdf"
    svc = storage()
    assert (
        await preview.ensure_preview(source, entry_id="entry", storage=svc)
        == "cached.pdf"
    )
    svc.read_attachment.assert_not_awaited()
    svc.get_metadata.assert_awaited_once_with("cached.pdf")


@pytest.mark.asyncio
async def test_preview_cancel_kills_and_reaps_converter_before_directory_cleanup(
    monkeypatch,
):
    started = asyncio.Event()
    killed = asyncio.Event()
    commands = []
    workdir = None
    calls = 0

    class Process:
        returncode = None

        async def communicate(self):
            nonlocal calls
            calls += 1
            started.set()
            await killed.wait()
            assert Path(workdir).exists()
            return b"", b""

        def kill(self):
            self.returncode = -9
            killed.set()

    async def spawn(*args, **kwargs):
        nonlocal workdir
        commands.extend(args)
        workdir = args[args.index("--outdir") + 1]
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(
        preview._convert_to_pdf("office", b"bytes", suffix=".doc")
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert killed.is_set() and calls == 2
    assert any(str(arg).startswith("-env:UserInstallation=file:") for arg in commands)
    assert not Path(workdir).exists()
