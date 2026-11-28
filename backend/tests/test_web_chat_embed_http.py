"""Detached embed HTTP cases using synthetic policy and ports, never a real host."""

from dataclasses import dataclass, field, replace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services.web_chat_browser_transport import (
    BrowserWebChatMessage,
    BrowserWebChatReply,
)
from app.web.web_chat_embed_http import (
    BoundEmbedSession,
    IssuedEmbedSession,
    create_web_chat_embed_router,
)
from app.web.web_chat_embed_policy import ManagedWebChatEmbedPolicy

BASE = "https://frame.example.test"
PARENT = "https://parent.example.test"
ROUTE = "fixture-route"
PATH = f"/api/public/web-chat-embed/{ROUTE}"
SEED = "iweb_" + "b" * 86
SESSION = "iwes_" + "s" * 86
POLICY = ManagedWebChatEmbedPolicy(
    ExternalChatProvision("test-workspace", "public_web_chat", "test-provision"),
    "11",
    "fixture-profile",
    2,
    4,
    PARENT,
    BASE,
    ROUTE,
)
HISTORY = BrowserWebChatReply(
    "ready",
    (
        BrowserWebChatMessage("visitor-one", "visitor", "Example question"),
        BrowserWebChatMessage("assistant-one", "assistant", "Example answer"),
    ),
)


def bearer(secret=SESSION):
    return {"Authorization": f"Bearer {secret}"}


@dataclass
class Fixture:
    policy: ManagedWebChatEmbedPolicy | None = POLICY
    ticket_policy: ManagedWebChatEmbedPolicy = POLICY
    active: bool = True
    spent: bool = False
    ttl: int = 120
    revoke_at: str = ""
    reply: BrowserWebChatReply = HISTORY
    calls: list = field(default_factory=list)

    async def resolve(self, reference):
        self.calls.append("resolve")
        return self.policy if reference == ROUTE else None

    async def current(self):
        self.calls.append("current")
        return self.active

    async def exchange(self, policy, seed):
        self.calls.append("exchange")
        if policy != POLICY or self.spent or seed != SEED:
            return None
        self.spent = True
        if self.revoke_at == "exchange":
            self.active = False
        return IssuedEmbedSession(self.ticket_policy, SESSION, self.ttl, self.current)

    async def read(self):
        self.calls.append("read")
        if self.revoke_at == "read":
            self.policy = replace(POLICY, policy_revision=POLICY.policy_revision + 1)
        return self.reply

    async def send(self, message_id, text):
        self.calls.append(("send", message_id, text))
        if self.revoke_at == "send":
            self.active = False
        return self.reply

    async def bind(self, policy, credential):
        self.calls.append("bind")
        if credential != SESSION or not self.spent:
            return None
        return BoundEmbedSession(
            self.ticket_policy, self.ttl, self.current, self.read, self.send
        )


def client(fixture, *, mount=True, base=BASE):
    app = FastAPI(redirect_slashes=False)
    router = create_web_chat_embed_router(
        resolve_policy=fixture.resolve, exchange=fixture.exchange, bind=fixture.bind
    )
    if mount:
        app.include_router(router)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url=base,
        headers={"Origin": BASE, "Sec-Fetch-Site": "same-origin"},
    )


def assert_safe(result, status, data):
    assert result.status_code == status
    assert result.json() == data
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert "set-cookie" not in result.headers
    assert "access-control-allow-origin" not in result.headers
    assert SEED not in result.text
    if "session_token" not in data:
        assert SESSION not in result.text


@pytest.mark.asyncio
async def test_exact_embed_exchange_then_resume_and_sanitized_send():
    fixture = Fixture()
    start = {"mode": "resume", "bootstrap_seed": SEED}
    async with client(fixture, mount=False) as api:
        assert (await api.post(f"{PATH}/session", json=start)).status_code == 404
    async with client(fixture) as api:
        first = await api.post(f"{PATH}/session", json=start)
        assert_safe(
            first,
            200,
            {
                "kind": "ready",
                "messages": [],
                "session_token": SESSION,
                "expires_in": 120,
            },
        )
        replay = await api.post(f"{PATH}/session", json=start)
        assert_safe(replay, 401, {"kind": "expired"})
        resumed = await api.post(
            f"{PATH}/session", json={"mode": "resume"}, headers=bearer()
        )
        assert_safe(
            resumed,
            200,
            {**HISTORY.to_wire(), "session_token": SESSION, "expires_in": 120},
        )
        sent = await api.post(
            f"{PATH}/messages",
            json={"id": "plain-id", "text": "Example\r\nmessage"},
            headers=bearer(),
        )
        assert_safe(sent, 200, HISTORY.to_wire())
        assert ("send", "plain-id", "Example\nmessage") in fixture.calls
        fixture.reply = BrowserWebChatReply("pending")
        pending = await api.post(
            f"{PATH}/messages",
            json={"id": "plain-id", "text": "Example\nmessage"},
            headers=bearer(),
        )
        assert_safe(pending, 200, {"kind": "pending"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "headers", "base", "status"),
    [
        (PATH, {"Origin": PARENT}, BASE, 403),
        (PATH, {"Origin": "null"}, BASE, 403),
        (PATH, {"Host": "other.example.test"}, BASE, 403),
        (PATH, {"Sec-Fetch-Site": "same-site"}, BASE, 403),
        (PATH, {"Cookie": "__Secure-integral_web_chat=unrelated"}, BASE, 400),
        (PATH, {}, "http://frame.example.test", 403),
        (PATH.replace("fixture-route", "%66ixture-route"), {}, BASE, 404),
        (PATH.replace("fixture-route", SEED), {}, BASE, 404),
    ],
)
async def test_wrong_request_boundary_never_calls_trusted_ports(
    path, headers, base, status
):
    fixture = Fixture()
    async with client(fixture, base=base) as api:
        result = await api.post(
            f"{path}/session",
            json={"mode": "resume", "bootstrap_seed": SEED},
            headers=headers,
        )
        assert_safe(result, status, {"kind": "unavailable"})
    assert fixture.calls == []


@pytest.mark.asyncio
async def test_quiet_shape_failures_and_no_credential_fallback():
    fixture = Fixture(spent=True)
    async with client(fixture) as api:
        invalid = [
            (
                f"{PATH}/session?mode=resume",
                {"json": {"mode": "resume"}, "headers": bearer()},
                400,
            ),
            (
                f"{PATH}/session",
                {
                    "json": {"mode": "resume", "bootstrap_seed": SEED},
                    "headers": bearer(),
                },
                400,
            ),
            (
                f"{PATH}/session",
                {"json": {"mode": "resume", "bootstrap_seed": SEED[:-1]}},
                400,
            ),
            (f"{PATH}/session", {"json": {"mode": "new"}, "headers": bearer()}, 403),
            (
                f"{PATH}/session",
                {
                    "json": {"mode": "resume", "bootstrap_seed": SEED},
                    "headers": bearer(SEED),
                },
                401,
            ),
            (
                f"{PATH}/messages",
                {
                    "json": {"id": "one", "text": "example"},
                    "headers": bearer("iwcs_" + "a" * 43),
                },
                401,
            ),
            (f"{PATH}/messages", {"json": {"id": "one", "text": "example"}}, 401),
            (
                f"{PATH}/messages",
                {"json": {"id": "one", "text": SEED}, "headers": bearer()},
                400,
            ),
            (
                f"{PATH}/messages",
                {"json": {"id": SEED, "text": "example"}, "headers": bearer()},
                400,
            ),
            (
                f"{PATH}/messages",
                {
                    "json": {"id": "one", "text": "example", "workspace_id": "foreign"},
                    "headers": bearer(),
                },
                400,
            ),
            (
                f"{PATH}/messages",
                {
                    "content": '{"id":"one","id":"two","text":"example"}',
                    "headers": {**bearer(), "Content-Type": "application/json"},
                },
                400,
            ),
            (
                f"{PATH}/messages",
                {
                    "content": "x" * 8193,
                    "headers": {**bearer(), "Content-Type": "application/json"},
                },
                413,
            ),
        ]
        for path, args, status in invalid:
            result = await api.post(path, **args)
            assert_safe(
                result, status, {"kind": "expired" if status == 401 else "unavailable"}
            )
    assert fixture.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["exchange", "read", "send"])
async def test_last_guard_hides_credentials_or_history_on_authority_change(change):
    fixture = Fixture(spent=change != "exchange", revoke_at=change)
    async with client(fixture) as api:
        if change == "exchange":
            response = await api.post(
                f"{PATH}/session", json={"mode": "new", "bootstrap_seed": SEED}
            )
        elif change == "read":
            response = await api.post(
                f"{PATH}/session", json={"mode": "resume"}, headers=bearer()
            )
        else:
            response = await api.post(
                f"{PATH}/messages", json={"id": "one", "text": "test"}, headers=bearer()
            )
        assert_safe(response, 401, {"kind": "expired"})
        assert "Example answer" not in response.text


@pytest.mark.asyncio
async def test_reject_policy_mismatch_bad_expiry_and_secret_like_reply():
    async with client(Fixture(ticket_policy=replace(POLICY, route_epoch="12"))) as api:
        bad = await api.post(
            f"{PATH}/session", json={"mode": "new", "bootstrap_seed": SEED}
        )
        assert_safe(bad, 503, {"kind": "unavailable"})
    async with client(Fixture(ttl=301)) as api:
        bad = await api.post(
            f"{PATH}/session", json={"mode": "new", "bootstrap_seed": SEED}
        )
        assert_safe(bad, 503, {"kind": "unavailable"})

    reply = BrowserWebChatReply(
        "ready", (BrowserWebChatMessage("example", "assistant", SEED),)
    )
    async with client(Fixture(spent=True, reply=reply)) as api:
        bad = await api.post(
            f"{PATH}/messages", json={"id": "one", "text": "test"}, headers=bearer()
        )
        assert_safe(bad, 503, {"kind": "unavailable"})
