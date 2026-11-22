"""Bounded anonymous visitor HTTP tests against a detached test-only ASGI app."""

from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services.web_chat_browser_transport import (
    BrowserWebChatMessage,
    BrowserWebChatReply,
)
from app.web.full_page_chat import (
    BoundVisitorController,
    TrustedFullPageRoute,
    VisitorCookieIssue,
    create_full_page_web_chat_router,
)

ORIGIN = "https://visit.example.test"
ROUTE = "/api/public/web-chat/Routed_opaque"
COOKIE = "__Secure-integral_web_chat"
TOKEN = "k" * 43 + "." + "j" * 43
NEXT_TOKEN = "m" * 43 + "." + "n" * 43


@dataclass
class FakePorts:
    """The router never resolves real workspace, credentials or provider."""

    resolved: list = field(default_factory=list)
    bound: list = field(default_factory=list)
    started: list = field(default_factory=list)
    sent: list = field(default_factory=list)
    issue: VisitorCookieIssue | None = VisitorCookieIssue(TOKEN, 86400)
    issue_reads: int = 0
    reply: BrowserWebChatReply = BrowserWebChatReply("ready")
    fault: bool = False
    provision: ExternalChatProvision = ExternalChatProvision(
        "private-workspace", "public_web_chat", "private-provision"
    )

    async def resolve(self, key):
        self.resolved.append(key)
        if self.fault:
            raise RuntimeError("private-workspace-secret")
        if key != "Routed_opaque":
            return None
        return TrustedFullPageRoute(self.provision, ORIGIN)

    async def bind(self, trusted, credential):
        self.bound.append((trusted.provision, credential))
        return BoundVisitorController(self, self.take_cookie)

    async def start(self, mode):
        self.started.append(mode)
        return self.reply

    async def send(self, event_id, text):
        self.sent.append((event_id, text))
        return self.reply

    def take_cookie(self):
        self.issue_reads += 1
        return self.issue


def app_with(ports, *, wire=True):
    app = FastAPI()
    router = create_full_page_web_chat_router(ports.resolve, ports.bind)
    if wire:
        app.include_router(router)
    return app


def client(ports, origin=ORIGIN, *, wire=True, headers=None):
    return AsyncClient(
        transport=ASGITransport(app=app_with(ports, wire=wire)),
        base_url=origin,
        headers={"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin", **(headers or {})},
    )


def assert_unavailable(response, status):
    assert response.status_code == status
    assert response.json() == {"kind": "unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "application/json"
    assert "set-cookie" not in response.headers
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_detached_post_only_and_no_embed_preflight():
    ports = FakePorts()
    async with client(ports, wire=False) as browser:
        assert (
            await browser.post(f"{ROUTE}/session", json={"mode": "resume"})
        ).status_code == 404
    async with client(ports) as browser:
        assert (await browser.get(f"{ROUTE}/session")).status_code == 405
        response = await browser.options(
            f"{ROUTE}/messages",
            headers={
                "Origin": "https://foreign.example.test",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code == 405
        assert "access-control-allow-origin" not in response.headers
    assert not ports.resolved


@pytest.mark.asyncio
async def test_start_issues_only_route_scoped_opaque_cookie_and_resume_can_reuse():
    ports = FakePorts(
        reply=BrowserWebChatReply(
            "ready", (BrowserWebChatMessage("a", "assistant", "<literal>"),)
        )
    )
    async with client(ports) as browser:
        response = await browser.post(f"{ROUTE}/session", json={"mode": "resume"})
        assert response.status_code == 200
        assert response.json() == {
            "kind": "ready",
            "messages": [{"id": "a", "role": "assistant", "text": "<literal>"}],
        }
        parts = response.headers["set-cookie"]
        assert f"{COOKIE}={TOKEN}" in parts
        assert f"Path={ROUTE}" in parts
        assert "Max-Age=86400" in parts
        assert "Secure" in parts
        assert "HttpOnly" in parts
        assert "SameSite=strict" in parts
        assert "Domain" not in parts
        assert TOKEN not in response.text
        assert "private-" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        ports.issue = None
        response = await browser.post(f"{ROUTE}/session", json={"mode": "resume"})
        assert response.status_code == 200
        assert "set-cookie" not in response.headers
        ports.issue = VisitorCookieIssue(NEXT_TOKEN, 300)
        response = await browser.post(f"{ROUTE}/session", json={"mode": "new"})
        assert f"{COOKIE}={NEXT_TOKEN}" in response.headers["set-cookie"]
    assert ports.bound == [
        (ports.provision, None),
        (ports.provision, TOKEN),
        (ports.provision, TOKEN),
    ]


@pytest.mark.asyncio
async def test_pending_and_expired_minimized_and_only_cookie_bound_messages_dispatch():
    ports = FakePorts(reply=BrowserWebChatReply("pending"))
    async with client(ports) as browser:
        expired = await browser.post(
            f"{ROUTE}/messages", json={"id": "valid-id", "text": "No cookie"}
        )
        assert expired.json() == {"kind": "expired"}
        assert not ports.bound
        response = await browser.post(
            f"{ROUTE}/messages",
            json={"id": "same-id", "text": "Exact \r\n text"},
            headers={"Cookie": f"other=unrelated; {COOKIE}={TOKEN}"},
        )
        assert response.json() == {"kind": "pending"}
        assert "set-cookie" not in response.headers
        assert ports.sent == [("same-id", "Exact \r\n text")]
        assert ports.issue_reads == 0
        response = await browser.post(f"{ROUTE}/session", json={"mode": "resume"})
        assert_unavailable(response, 503)  # pending cannot originate from session start
        ports.reply = BrowserWebChatReply("expired")
        response = await browser.post(f"{ROUTE}/session", json={"mode": "resume"})
        assert response.json() == {"kind": "expired"}
        assert "set-cookie" not in response.headers
        assert ports.issue_reads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("base_url", "headers"),
    [
        (ORIGIN, {"Origin": ""}),
        (ORIGIN, {"Origin": "null"}),
        (ORIGIN, {"Origin": "https://foreign.example.test"}),
        ("https://foreign.example.test", {"Origin": "https://foreign.example.test"}),
        (ORIGIN, {"Origin": "https://visit.example.test:443"}),
        (ORIGIN, {"Origin": "https://visit.example.test:99999"}),
        ("http://visit.example.test", {"X-Forwarded-Proto": "https"}),
        (ORIGIN, {"Sec-Fetch-Site": "same-site"}),
        (ORIGIN, {"Sec-Fetch-Site": "cross-site"}),
    ],
)
async def test_untrusted_origin_never_reaches_controller(base_url, headers):
    ports = FakePorts()
    async with client(ports, base_url, headers=headers) as browser:
        assert_unavailable(
            await browser.post(f"{ROUTE}/session", json={"mode": "resume"}), 403
        )
    assert not ports.bound


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "raw", "extra", "status"),
    [
        (f"{ROUTE}/session", b'{"mode":"new","workspace_id":"private"}', {}, 400),
        (f"{ROUTE}/session", b'{"mode":"new","credential":"private"}', {}, 400),
        (f"{ROUTE}/session", b'{"mode":"new","mode":"resume"}', {}, 400),
        (f"{ROUTE}/session", b'{"mode":false}', {}, 400),
        (f"{ROUTE}/session", b'{"mode":"new"} trailing', {}, 400),
        (f"{ROUTE}/session", b'{"mode":"\xff"}', {}, 400),
        (f"{ROUTE}/session", b'{"mode":"new"}', {"Content-Type": "text/plain"}, 415),
        (f"{ROUTE}/session", b'{"mode":"new"}', {"Content-Encoding": "gzip"}, 415),
        (f"{ROUTE}/session?profile=secret", b'{"mode":"new"}', {}, 400),
        (
            f"{ROUTE}/session",
            b'{"mode":"new"}',
            {"Authorization": "Bearer secret"},
            400,
        ),
        (
            f"{ROUTE}/session",
            b'{"mode":"new"}',
            {"Cookie": f"{COOKIE}={TOKEN}; {COOKIE}={NEXT_TOKEN}"},
            400,
        ),
        (f"{ROUTE}/session", b'{"mode":"new"}', {"Cookie": f"{COOKIE}=bad"}, 400),
        (f"{ROUTE}/session", b'{"mode":"new"}', {"Content-Length": "1"}, 400),
        (f"{ROUTE}/session", b'{"mode":"new"}', {"Content-Length": "9000"}, 413),
        ("/api/public/web-chat/Bad%20Slug/session", b'{"mode":"new"}', {}, 404),
        ("/api/public/web-chat/%52outed_opaque/session", b'{"mode":"new"}', {}, 404),
        (f"{ROUTE}/messages", b'{"id":"valid","text":"abc","profile":"bad"}', {}, 400),
        (f"{ROUTE}/messages", b'{"id":"invalid/id","text":"abc"}', {}, 400),
        (f"{ROUTE}/messages", rb'{"id":"okay","text":" \u0000 "}', {}, 400),
        (f"{ROUTE}/messages", b'{"id":"okay","text":"    "}', {}, 400),
        (f"{ROUTE}/messages", b'{"id":"okay","text":"' + b"a" * 1001 + b'"}', {}, 400),
    ],
)
async def test_strict_validation_before_trusted_callbacks(path, raw, extra, status):
    ports = FakePorts()
    async with client(ports) as browser:
        response = await browser.post(
            path, content=raw, headers={"Content-Type": "application/json", **extra}
        )
        assert_unavailable(response, status)
    assert not ports.resolved and not ports.bound


@pytest.mark.asyncio
async def test_streaming_byte_limit_without_length_header():
    ports = FakePorts()
    consumed = []

    async def body():
        for index in range(8):
            consumed.append(index)
            yield b"x" * 4000

    async with client(ports) as browser:
        response = await browser.post(
            f"{ROUTE}/messages",
            content=body(),
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
        assert_unavailable(response, 413)
    assert consumed == [0, 1, 2]
    assert not ports.bound


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["resolver-fault", "bad-reply", "missing-issue", "same-token", "bad-ttl"]
)
async def test_redacted_errors_and_no_cookie_on_uncertain_session(case):
    ports = FakePorts()
    if case == "resolver-fault":
        ports.fault = True
    elif case == "bad-reply":
        ports.reply = BrowserWebChatReply(
            "ready", (BrowserWebChatMessage("invalid:id", "assistant", "secret"),)
        )
    elif case == "missing-issue":
        ports.issue = None
    elif case == "bad-ttl":
        ports.issue = VisitorCookieIssue(NEXT_TOKEN, 999_999_999)
    incoming = {"Cookie": f"{COOKIE}={TOKEN}"} if case == "same-token" else {}
    async with client(ports) as browser:
        response = await browser.post(
            f"{ROUTE}/session", headers=incoming, json={"mode": "new"}
        )
        assert_unavailable(response, 503)
