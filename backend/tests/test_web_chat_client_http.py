"""Wire contract only: fictional grants, isolated ASGI, no real host or model."""

from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agentive.services.web_chat_browser_transport import (
    BrowserWebChatMessage,
    BrowserWebChatReply,
)
from app.web.web_chat_client_http import (
    BoundClientSession,
    IssuedClientSession,
    create_web_chat_client_router,
)

ORIGIN = "https://api.example.test"
GRANT = "iwcg_" + "g" * 43
SESSION = "iwcs_" + "s" * 43
OTHER_SESSION = "iwcs_" + "t" * 43
START, READ, SEND = (
    "/api/v1/web-chat/" + s for s in ("sessions", "session", "session/messages")
)


def auth(credential):
    return {"Authorization": f"Bearer {credential}"}


@dataclass
class Fixture:
    active: bool = True
    end_on_call: bool = False
    credential_to_issue: str = field(default=SESSION, repr=False)
    ttl: int = 300
    calls: list = field(default_factory=list)
    reply: BrowserWebChatReply = BrowserWebChatReply(
        "ready", (BrowserWebChatMessage("test", "assistant", "Plain example"),)
    )

    async def current(self):
        return self.active

    async def issue(self, credential):
        self.calls.append("issue")
        if credential != GRANT:
            return None
        return IssuedClientSession(self.credential_to_issue, self.ttl, self.current)

    async def bind(self, credential):
        self.calls.append("bind")
        if credential not in (SESSION, OTHER_SESSION):
            return None

        async def read():
            if credential == OTHER_SESSION:
                return BrowserWebChatReply("ready")
            if self.end_on_call:
                self.active = False
            return self.reply

        async def send(event_id, message):
            self.calls.append(("send", event_id, message))
            return await read()

        return BoundClientSession(read, send, self.current)


def client(fixture, *, mount=True, origin=ORIGIN):
    router = create_web_chat_client_router(
        origin=ORIGIN, start=fixture.issue, bind=fixture.bind
    )
    app = FastAPI(redirect_slashes=False)
    if mount:
        app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url=origin)


def assert_safe(response, status, data=None):
    assert response.status_code == status
    assert response.json() == ({"kind": "unavailable"} if data is None else data)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "set-cookie" not in response.headers
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_correct_bearer_class_and_no_browser_crossover():
    fixture = Fixture()
    async with client(fixture, mount=False) as api:
        assert (await api.post(START, headers=auth(GRANT))).status_code == 404
    async with client(fixture) as api:
        created = await api.post(START, headers=auth(GRANT))
        assert_safe(
            created, 201, {"session_token": SESSION, "expires_in": 300, "messages": []}
        )
        assert GRANT not in created.text
        response = await api.get(READ, headers=auth(SESSION))
        assert_safe(
            response,
            200,
            {
                "kind": "ready",
                "messages": [
                    {"id": "test", "role": "assistant", "text": "Plain example"}
                ],
            },
        )
        assert SESSION not in response.text
        assert_safe(
            await api.get(READ, headers=auth(OTHER_SESSION)),
            200,
            {"kind": "ready", "messages": []},
        )
        assert_safe(
            await api.post(
                SEND, headers=auth(SESSION), json={"id": "evt_1", "text": "Hi"}
            ),
            200,
            response.json(),
        )
        assert_safe(await api.post(START, headers=auth(SESSION)), 401)
        assert_safe(await api.get(READ, headers=auth(GRANT)), 401)
        assert_safe(await api.get(READ), 401)
        assert (await api.options(SEND, headers={"Origin": ORIGIN})).status_code == 405
        assert (await api.get(READ + "/", headers=auth(SESSION))).status_code == 404
    assert fixture.calls == ["issue", "bind", "bind", "bind", ("send", "evt_1", "Hi")]


@pytest.mark.parametrize("header", ["Origin", "Cookie", "Sec-Fetch-Site"])
@pytest.mark.asyncio
async def test_deny_browser_context_before_credential_ports(header):
    fixture = Fixture()
    async with client(fixture) as api:
        response = await api.get(READ, headers={**auth(SESSION), header: "x"})
        assert_safe(response, 403)
    assert not fixture.calls


@pytest.mark.asyncio
async def test_no_query_scope_spoof_body_duplicate_keys_or_oversize_request():
    fixture = Fixture()
    async with client(fixture) as api:
        assert_safe(await api.get(READ + "?token=test", headers=auth(SESSION)), 400)
        assert_safe(
            await api.get(
                READ, headers=[*auth(SESSION).items(), *auth(OTHER_SESSION).items()]
            ),
            400,
        )
        assert_safe(await api.post(START, headers=auth(GRANT), json={}), 400)
        for payload in (
            b'{"id":"evt","id":"again","text":"hello"}',
            b'{"id":"evt","text":"hello","workspace":"untrusted"}',
            b'{"id":"evt","text":"   "}',
        ):
            assert_safe(
                await api.post(
                    SEND,
                    headers={
                        **auth(SESSION),
                        "Content-Type": "application/json",
                    },
                    content=payload,
                ),
                400,
            )
        assert_safe(
            await api.post(
                SEND, headers=auth(SESSION), json={"id": "evt", "text": "a" * 9000}
            ),
            413,
        )
    for unsafe_origin in ("http://api.example.test", "https://wrong.example.test"):
        async with client(fixture, origin=unsafe_origin) as api:
            assert_safe(await api.get(READ, headers=auth(SESSION)), 403)
    assert not fixture.calls


@pytest.mark.parametrize("action", ["start", "read", "send"])
@pytest.mark.asyncio
async def test_after_revocation_no_new_token_or_protected_output(action):
    fixture = Fixture(active=action != "start", end_on_call=True)
    async with client(fixture) as api:
        if action == "start":
            response = await api.post(START, headers=auth(GRANT))
        elif action == "read":
            response = await api.get(READ, headers=auth(SESSION))
        else:
            response = await api.post(
                SEND, headers=auth(SESSION), json={"id": "evt", "text": "Hello"}
            )
        assert_safe(response, 401)
        assert_safe(await api.get(READ, headers=auth(SESSION)), 401)
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    ("issued", "ttl"), [(GRANT, 300), (SESSION, 901), (SESSION, True), (SESSION, 0)]
)
@pytest.mark.asyncio
async def test_reject_invalid_minted_credential_shape_or_lifetime(issued, ttl):
    fixture = Fixture(credential_to_issue=issued, ttl=ttl)
    async with client(fixture) as api:
        assert_safe(await api.post(START, headers=auth(GRANT)), 503)


@pytest.mark.asyncio
async def test_bound_transport_does_not_leak_private_failures():
    fixture = Fixture()

    async def bad_bind(_):
        raise RuntimeError("credential and provider secret")

    router = create_web_chat_client_router(
        origin=ORIGIN, start=fixture.issue, bind=bad_bind
    )
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as api:
        assert_safe(await api.get(READ, headers=auth(SESSION)), 503)
