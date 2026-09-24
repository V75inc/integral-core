"""Serve the built React workspace and proxy the API.

``integral web`` listens on port 9006. The browser talks only to that
port. ``/api`` and WebSocket paths are forwarded to the API process.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.websockets import WebSocketState

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
}

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# Missing-asset 404 only for known static suffixes. Entity deep links use
# jvspatial ids in the last segment (``n.Track.…``, ``n.Entry.…``) which
# contain dots — ``"." in Path(path).name`` would false-positive those as
# assets and return a blank 404 on hard refresh / direct open.
_STATIC_ASSET_SUFFIXES = frozenset(
    {
        ".css",
        ".eot",
        ".gif",
        ".html",
        ".ico",
        ".jpeg",
        ".jpg",
        ".js",
        ".json",
        ".map",
        ".png",
        ".svg",
        ".ttf",
        ".txt",
        ".wasm",
        ".webp",
        ".woff",
        ".woff2",
    }
)


def web_static_dir() -> Path | None:
    """Return the built workspace directory, when this install has one."""
    if (_STATIC_DIR / "index.html").is_file():
        return _STATIC_DIR
    return None


def _missing_frontend() -> FileNotFoundError:
    return FileNotFoundError(
        "this install has no built frontend. "
        "From a Core checkout: cd frontend && npm install && npm run dev"
    )


def _ws_base(api_base: str) -> str:
    trimmed = api_base.rstrip("/")
    if trimmed.startswith("https://"):
        return "wss://" + trimmed[len("https://") :]
    if trimmed.startswith("http://"):
        return "ws://" + trimmed[len("http://") :]
    return trimmed


def _upstream_url(api_base: str, prefix: str, path: str, query: str) -> str:
    url = f"{api_base.rstrip('/')}/{prefix}/{path}"
    if query:
        return f"{url}?{query}"
    return url


def _forward_headers(headers) -> dict[str, str]:
    return {
        key: value for key, value in headers.items() if key.lower() not in _HOP_BY_HOP
    }


def _safe_file(static_dir: Path, rel: str) -> Path | None:
    if not rel or rel.endswith("/"):
        return None
    root = static_dir.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def create_web_app(
    static_dir: Path,
    api_base: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Workspace app: static files, plus a proxy for ``/api`` and WebSockets."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.client is None:
            app.state.client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=None, write=None, pool=10.0),
                follow_redirects=False,
            )
            app.state.owns_client = True
        try:
            yield
        finally:
            if app.state.owns_client:
                await app.state.client.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.client = client
    app.state.owns_client = False
    app.state.api_base = api_base.rstrip("/")
    app.state.static_dir = static_dir

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def proxy_api(path: str, request: Request) -> Response:
        return await _proxy_http(request, "api", path)

    @app.websocket("/api/{path:path}")
    async def proxy_api_ws(websocket: WebSocket, path: str) -> None:
        target = _upstream_url(
            _ws_base(app.state.api_base), "api", path, websocket.url.query
        )
        await _proxy_websocket(websocket, target)

    @app.websocket("/ws/{path:path}")
    async def proxy_ws(websocket: WebSocket, path: str) -> None:
        target = _upstream_url(
            _ws_base(app.state.api_base), "ws", path, websocket.url.query
        )
        await _proxy_websocket(websocket, target)

    @app.api_route("/", methods=["GET"])
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.api_route("/{path:path}", methods=["GET"])
    async def asset_or_spa(path: str) -> Response:
        found = _safe_file(static_dir, path)
        if found is not None:
            return FileResponse(found)
        if Path(path).suffix.lower() in _STATIC_ASSET_SUFFIXES:
            return Response(status_code=404)
        return FileResponse(static_dir / "index.html")

    return app


async def _proxy_http(request: Request, prefix: str, path: str) -> Response:
    url = _upstream_url(request.app.state.api_base, prefix, path, request.url.query)
    try:
        upstream = await request.app.state.client.send(
            request.app.state.client.build_request(
                request.method,
                url,
                headers=_forward_headers(request.headers),
                content=request.stream(),
            ),
            stream=True,
        )
    except httpx.RequestError:
        return Response(status_code=502, content="API unreachable")

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=_forward_headers(upstream.headers),
    )


async def _proxy_websocket(websocket: WebSocket, target: str) -> None:
    await websocket.accept()
    try:
        async with websockets.connect(target) as upstream:
            incoming = asyncio.create_task(_client_to_upstream(websocket, upstream))
            outgoing = asyncio.create_task(_upstream_to_client(websocket, upstream))
            done, pending = await asyncio.wait(
                {incoming, outgoing}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
    except Exception:
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close(code=1011)


async def _client_to_upstream(websocket: WebSocket, upstream) -> None:
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                await upstream.send(message["text"])
            elif message.get("bytes") is not None:
                await upstream.send(message["bytes"])
    except WebSocketDisconnect:
        return


async def _upstream_to_client(websocket: WebSocket, upstream) -> None:
    async for payload in upstream:
        if isinstance(payload, str):
            await websocket.send_text(payload)
        else:
            await websocket.send_bytes(payload)


def serve_web(*, host: str, port: int, api_base: str) -> None:
    """Block and serve the workspace. Raises when the build is absent."""
    import uvicorn

    static_dir = web_static_dir()
    if static_dir is None:
        raise _missing_frontend()
    print(f"Workspace at http://{host}:{port}", flush=True)
    print(f"Proxying /api and /ws to {api_base}", flush=True)
    uvicorn.run(
        create_web_app(static_dir, api_base),
        host=host,
        port=port,
        log_level="info",
    )
