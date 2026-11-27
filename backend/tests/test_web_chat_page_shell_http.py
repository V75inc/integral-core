"""Test detached HTML and exact released assets in an isolated ASGI app."""

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.web.web_chat_host_assembly import WebChatPageContext
from app.web.web_chat_page_shell import (
    StandaloneWebChatAssets,
    create_web_chat_page_shell_router,
)

ORIGIN = "https://visitor.example.invalid"
ROUTE = "opaque-route"
TITLE = 'Welcome <there> & "friend"'
SCRIPT = "/web-chat-assets/assets/public-web-chat-1234abcd.js"
CSS = "/web-chat-assets/assets/public-web-chat-1234abcd.css"
BUILD = f'<script type="module" src="{SCRIPT}"></script><link rel="stylesheet" href="{CSS}">'


def _build(root):
    (root / "assets").mkdir()
    (root / "public-web-chat.html").write_text(BUILD)
    (root / "assets" / SCRIPT.rsplit("/", 1)[1]).write_bytes(b"/* JS fixture */")
    (root / "assets" / CSS.rsplit("/", 1)[1]).write_bytes(b"/* CSS fixture */")
    return root


def _app(assets, ready, context):
    app = FastAPI()
    app.include_router(
        create_web_chat_page_shell_router(
            served_origin=ORIGIN,
            host_ready=ready,
            page_context=context,
            assets=assets,
        )
    )
    return app


@pytest.fixture
def ports(tmp_path):
    """Use a trusted-offline artifact fixture and changing read-only policy."""
    assets = StandaloneWebChatAssets.from_vite_build(_build(tmp_path.resolve()))
    return (
        assets,
        AsyncMock(return_value=True),
        AsyncMock(return_value=WebChatPageContext(ROUTE, TITLE)),
    )


@pytest.mark.asyncio
async def test_html_uses_safe_projection_and_exact_assets(ports):
    """Escape the title and serve only the two preloaded content assets."""
    async with AsyncClient(
        transport=ASGITransport(app=_app(*ports)), base_url=ORIGIN
    ) as browser:
        result = await browser.get(f"/chat/{ROUTE}")
        assert result.status_code == 200
        assert "Welcome &lt;there&gt; &amp; &quot;friend&quot;" in result.text
        assert f'data-web-chat-route="{ROUTE}"' in result.text
        assert '<script type="module"' in result.text
        assert "sha384-" in result.text
        assert result.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in result.headers["content-security-policy"]
        assert result.headers["referrer-policy"] == "no-referrer"
        assert "set-cookie" not in result.headers
        script = await browser.get(SCRIPT)
        css = await browser.get(CSS)
        assert script.content == b"/* JS fixture */"
        assert css.content == b"/* CSS fixture */"
        assert script.headers["content-type"].startswith("text/javascript")
        assert css.headers["content-type"].startswith("text/css")
        for path in (
            "/web-chat-assets/public-web-chat.html",
            SCRIPT + ".map",
            SCRIPT + "?other=1",
        ):
            assert (await browser.get(path)).status_code == 404
        assert (await browser.post(f"/chat/{ROUTE}")).status_code == 405


@pytest.mark.asyncio
async def test_readiness_and_current_route_rechecked_before_html(ports):
    """A removed route or lost approved host yields no document context."""
    _, ready, context = ports
    async with AsyncClient(
        transport=ASGITransport(app=_app(*ports)), base_url=ORIGIN
    ) as browser:
        context.side_effect = [WebChatPageContext(ROUTE), None]
        denied = await browser.get(f"/chat/{ROUTE}")
        assert denied.status_code == 404 and ROUTE not in denied.text
        context.reset_mock()
        ready.return_value = False
        assert (await browser.get(f"/chat/{ROUTE}")).status_code == 404
        assert (await browser.get(SCRIPT)).status_code == 404
        context.assert_not_awaited()
        ready.reset_mock()
        assert (
            await browser.get(
                f"/chat/{ROUTE}", headers={"Host": "other.example.invalid"}
            )
        ).status_code == 404
        ready.assert_not_awaited()


def test_unknown_or_nonstandalone_build_cannot_be_loaded(tmp_path):
    """Reject wrong entries or unavailable approved assets at construction."""
    home = _build(tmp_path.resolve())
    (home / "public-web-chat.html").write_text(
        BUILD.replace(SCRIPT, "/assets/unrelated.js")
    )
    with pytest.raises(ValueError, match="Invalid standalone"):
        StandaloneWebChatAssets.from_vite_build(home)
    (home / "public-web-chat.html").write_text(BUILD)
    (home / "assets" / "unknown.js").write_bytes(b"not an approved entry")
    with pytest.raises(ValueError, match="Unexpected standalone"):
        StandaloneWebChatAssets.from_vite_build(home)


@pytest.mark.asyncio
async def test_deployment_can_serve_actual_built_vite_artifact():
    """Opt-in proof against the actual built standalone React/assistant-ui bundle."""
    directory = os.environ.get("INTEGRAL_TEST_WEB_CHAT_DIST")
    if not directory:
        pytest.skip("Set INTEGRAL_TEST_WEB_CHAT_DIST to the reviewed Vite output")
    assets = StandaloneWebChatAssets.from_vite_build(Path(directory).resolve())
    ready = AsyncMock(return_value=True)
    context = AsyncMock(return_value=WebChatPageContext(ROUTE))
    async with AsyncClient(
        transport=ASGITransport(app=_app(assets, ready, context)), base_url=ORIGIN
    ) as browser:
        page = await browser.get(f"/chat/{ROUTE}")
        assert page.status_code == 200
        for asset in (assets.script, assets.stylesheet):
            filename = f"/web-chat-assets/assets/{asset.filename}"
            assert filename in page.text
            body = await browser.get(filename)
            assert body.status_code == 200 and body.content == asset.content
