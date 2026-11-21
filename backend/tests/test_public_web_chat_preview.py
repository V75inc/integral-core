"""Defensive checks for the standalone loopback preview and its memory store."""

import json
from contextlib import contextmanager
from http.client import HTTPConnection
from threading import Thread

import pytest

from scripts.public_web_chat_preview import server as preview


@contextmanager
def running_preview(channel):
    with preview.PreviewServer(0, channel) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=2)


def request(server, suffix="", *, body=None, cookie="", origin=None):
    connection = HTTPConnection(server.authority, timeout=3)
    headers = {"Cookie": cookie}
    if body is not None:
        headers.update(
            {
                "Content-Type": "application/json",
                "Origin": origin or f"http://{server.authority}",
            }
        )
        body = json.dumps(body)
    try:
        connection.request(
            "POST" if body is not None else "GET", preview.ROOT + suffix, body, headers
        )
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


def create_session(server, previous=""):
    status, headers, body = request(server, "/session", body={}, cookie=previous)
    assert status == 201
    return headers["Set-Cookie"].partition(";")[0], json.loads(body)["conversation"]


def test_disabled_preview_serves_no_page_session_or_link():
    with running_preview(preview.PreviewChannel()) as server:
        status, headers, body = request(server)
        assert status == 404
        assert headers["Set-Cookie"] is None
        assert json.loads(body) == {"error": "Preview unavailable."}
        assert request(server, "/session", body={})[0] == 404


def test_preview_keeps_visitor_history_and_reset_scoped():
    channel = preview.PreviewChannel(enabled=True)
    with running_preview(channel) as server:
        status, headers, page = request(server)
        assert status == 200
        assert b"Local preview using sample replies" in page
        assert headers["Cache-Control"] == "no-store"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert request(server, "/session")[0] == 401
        assert (
            request(server, "/session", body={}, origin="http://localhost:9999")[0]
            == 403
        )
        assert not channel.sessions
        cedar, first = create_session(server)
        maple, second = create_session(server)
        assert cedar != maple
        assert first != second
        body = {"conversation": first, "message": "Hello"}
        status, _, reply = request(server, "/message", body=body, cookie=cedar)
        assert status == 200
        assert json.loads(reply)["messages"] == [
            {"role": "visitor", "text": "Hello"},
            {
                "role": "assistant",
                "text": "Hello! You’re chatting with the local preview assistant.",
            },
        ]
        assert request(server, "/session", cookie=cedar)[2] == reply
        assert (
            json.loads(request(server, "/session", cookie=maple)[2])["messages"] == []
        )
        new_cedar, own_new = create_session(server, cedar)
        assert request(server, "/session", cookie=cedar)[0] == 401
        assert request(server, "/message", body=body, cookie=new_cedar)[0] == 412
        assert json.loads(request(server, "/session", cookie=new_cedar)[2]) == {
            "conversation": own_new,
            "messages": [],
        }
        assert request(server, "/session", cookie=maple)[0] == 200
        channel.enabled = False
        assert request(server, "/session", cookie=maple)[0] == 404


@pytest.mark.smoke
def test_hosted_and_embedded_presentations_use_one_channel_and_visitor_session():
    channel = preview.PreviewChannel(enabled=True)
    with running_preview(channel) as server:
        _, hosted_headers, _ = request(server)
        status, embed_headers, embedded = request(server, "/embed")
        assert status == 200
        assert b'class="embedded"' in embedded
        assert "frame-ancestors 'none'" in hosted_headers["Content-Security-Policy"]
        assert "frame-ancestors 'self'" in embed_headers["Content-Security-Policy"]
        status, host_headers, host = request(server, "/embed-demo")
        assert status == 200
        assert b'src="/public-web-chat-preview/embed"' in host
        assert "frame-src 'self'" in host_headers["Content-Security-Policy"]
        cookie, conversation = create_session(server)
        body = {"conversation": conversation, "message": "Hello"}
        response = request(server, "/message", body=body, cookie=cookie)
        assert response[0] == 200
        assert request(server, "/session", cookie=cookie)[2] == response[2]
        other, _ = create_session(server)
        assert (
            json.loads(request(server, "/session", cookie=other)[2])["messages"] == []
        )
        channel.enabled = False
        assert request(server)[0] == 404
        assert request(server, "/embed")[0] == 404
        assert request(server, "/embed-demo")[0] == 404
        assert request(server, "/message", body=body, cookie=cookie)[0] == 404


def test_preview_requests_are_bounded_and_do_not_accept_scope_or_tools():
    with running_preview(preview.PreviewChannel(enabled=True)) as server:
        visitor, conversation = create_session(server)
        body = {"conversation": conversation, "message": "Hello"}
        for bad_body, expected in (
            ({**body, "message": "x" * 4096}, 413),
            ({**body, "message": "x" * 1001}, 400),
            ({**body, "message": " "}, 400),
            ({**body, "tools": ["app.read"]}, 400),
            ({**body, "workspace": "staff"}, 400),
        ):
            assert (
                request(server, "/message", body=bad_body, cookie=visitor)[0]
                == expected
            )
        assert (
            json.loads(request(server, "/session", cookie=visitor)[2])["messages"] == []
        )


def test_preview_rate_expiry_and_capacity(monkeypatch):
    now = 0
    channel = preview.PreviewChannel(enabled=True, clock=lambda: now)
    monkeypatch.setattr(preview, "SESSION_STARTS_PER_MINUTE", 3)
    monkeypatch.setattr(preview, "MESSAGES_PER_MINUTE", 1)
    monkeypatch.setattr(preview, "MAX_SESSIONS", 2)
    monkeypatch.setattr(preview, "MAX_TURNS", 2)
    first = channel.start("")
    second = channel.start("")
    with pytest.raises(preview.PreviewError) as err:
        channel.start("")
    assert err.value.status == 503
    with pytest.raises(preview.PreviewError) as err:
        channel.start("")
    assert err.value.status == 429
    body = {"conversation": channel.session(first).conversation, "message": "Hello"}
    assert len(channel.send(first, body)["messages"]) == 2
    with pytest.raises(preview.PreviewError) as err:
        channel.send(first, body)
    assert err.value.status == 429
    now += 60
    assert len(channel.send(first, body)["messages"]) == 4
    with pytest.raises(preview.PreviewError) as err:
        channel.send(first, body)
    assert err.value.status == 409
    now += preview.SESSION_TTL
    with pytest.raises(preview.PreviewError) as err:
        channel.session(first)
    assert err.value.status == 401
    assert channel.start("") != second
    assert len(channel.sessions) == 1
