"""integral web serves the workspace and proxies the API."""

import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient

from app.cli import main
from app.web.server import create_web_app


def _static(tmp_path: Path) -> Path:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<title>Integral</title>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return static


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_web_accepts_a_distro_path_and_reads_its_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "my-integral"
    dest.mkdir()
    (dest / ".env").write_text("JVSPATIAL_PORT=4010\n", encoding="utf-8")
    monkeypatch.delenv("JVSPATIAL_PORT", raising=False)
    seen: dict[str, object] = {}

    def fake_serve(**kwargs: object) -> None:
        seen.update(kwargs)

    import app.web.server as server

    monkeypatch.setattr(server, "serve_web", fake_serve)
    code = main(["web", str(dest), "--port", "9016"])
    assert code == 0
    assert seen["port"] == 9016
    assert seen["api_base"] == "http://127.0.0.1:4010"
    code = main(["web", str(dest), "--api", "http://127.0.0.1:3999"])
    assert code == 0
    assert seen["api_base"] == "http://127.0.0.1:3999"


def test_web_rejects_a_missing_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope"
    code = main(["web", str(missing)])
    assert code == 1
    assert "not a directory" in capsys.readouterr().err


def test_cli_reports_a_missing_frontend(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import app.web.server as server

    monkeypatch.setattr(server, "web_static_dir", lambda: None)
    code = main(["web", "--port", "9"])
    captured = capsys.readouterr()
    assert code == 1
    assert "no built frontend" in captured.err
    assert "npm run dev" in captured.err


def test_serves_index_assets_and_spa_fallback(tmp_path: Path) -> None:
    stub = FastAPI()

    @stub.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @stub.post("/api/echo")
    async def echo(request: Request) -> dict:
        return {"n": len(await request.body())}

    transport = httpx.ASGITransport(app=stub)
    client = httpx.AsyncClient(transport=transport, base_url="http://api")
    app = create_web_app(_static(tmp_path), "http://api", client=client)
    with TestClient(app) as web:
        index = web.get("/")
        assert index.status_code == 200
        assert "Integral" in index.text
        asset = web.get("/assets/app.js")
        assert asset.status_code == 200
        assert "console.log" in asset.text
        missing = web.get("/assets/missing.js")
        assert missing.status_code == 404
        route = web.get("/mission")
        assert route.status_code == 200
        assert "Integral" in route.text
        # jvspatial entity ids contain dots in the last segment — must still
        # SPA-fallback to index.html (not blank 404 as if a missing .js).
        for deep in (
            "/tracks/n.Track.abc123",
            "/entries/n.Entry.abc123",
            "/apps/n.WorkspaceApp.abc123",
        ):
            spa = web.get(deep)
            assert spa.status_code == 200, deep
            assert "Integral" in spa.text, deep
        health = web.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True
        body = b"x" * (2 * 1024 * 1024)
        echoed = web.post("/api/echo", content=body)
        assert echoed.status_code == 200
        assert echoed.json()["n"] == len(body)


def test_proxies_websocket(tmp_path: Path) -> None:
    stub = FastAPI()

    @stub.websocket("/ws/ping")
    async def ping(websocket: WebSocket) -> None:
        await websocket.accept()
        message = await websocket.receive_text()
        await websocket.send_text(message.upper())

    port = _free_port()
    thread = threading.Thread(
        target=uvicorn.run,
        kwargs={
            "app": stub,
            "host": "127.0.0.1",
            "port": port,
            "log_level": "warning",
        },
        daemon=True,
    )
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        raise RuntimeError("stub API did not listen")
    app = create_web_app(_static(tmp_path), f"http://127.0.0.1:{port}")
    with TestClient(app) as web:
        with web.websocket_connect("/ws/ping") as ws:
            ws.send_text("hi")
            assert ws.receive_text() == "HI"
