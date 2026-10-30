"""Native search authority is a verified session, never a caller-supplied flag."""

import time

import jwt
import pytest
from starlette.requests import Request

from app.api.utils import is_native_session_request
from app.config import settings

pytestmark = pytest.mark.smoke


@pytest.fixture(autouse=True)
def session_key(monkeypatch):
    monkeypatch.setattr(
        settings, "SECRET_KEY", "native-search-unit-test-signing-key" * 2
    )
    monkeypatch.setattr(settings, "ALGORITHM", "HS256")


def request_for(claims=None, *, key=None, algorithm="HS256"):
    payload = {
        "user_id": "o.User.interactive",
        "jti": "test-session-id",
        "exp": int(time.time()) + 3600,
    }
    payload.update(claims or {})
    token = jwt.encode(payload, key or settings.SECRET_KEY, algorithm=algorithm)
    request = Request(
        {"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]}
    )
    request.state.user = {"id": "o.User.interactive"}
    return request


def test_recognizes_signed_session_for_current_authenticated_principal():
    assert is_native_session_request(request_for(), "o.User.interactive")
    assert not is_native_session_request(request_for(), "o.User.someone-else")


@pytest.mark.parametrize(
    "claims",
    [
        {"svc_auth": True},
        {"jti": ""},
        {"jti": None},
        {"exp": int(time.time()) - 3600},
        {"user_id": "o.User.someone-else"},
        {"user_id": None, "sub": "o.User.interactive", "scope": "integral:read"},
    ],
)
def test_service_oauth_and_invalid_session_claims_do_not_open_native_read(claims):
    assert not is_native_session_request(request_for(claims), "o.User.interactive")


@pytest.mark.parametrize("actor", ["agent", "connector", "system", "unknown"])
def test_server_machine_actor_remains_governed_even_with_session_token(actor):
    request = request_for()
    request.state.actor_kind = actor
    assert not is_native_session_request(request, "o.User.interactive")


def test_server_service_auth_marker_remains_governed():
    request = request_for()
    request.state.service_auth = True
    assert not is_native_session_request(request, "o.User.interactive")


@pytest.mark.parametrize("algorithm", ["HS384", "HS512"])
def test_other_signature_algorithms_cannot_open_native_read(algorithm):
    assert not is_native_session_request(
        request_for(algorithm=algorithm), "o.User.interactive"
    )


def test_forged_session_and_unsigned_tokens_do_not_open_native_read():
    assert not is_native_session_request(
        request_for(key="different-signing-key-for-forgery"), "o.User.interactive"
    )
    token = jwt.encode(
        {"user_id": "o.User.interactive", "jti": "fake"}, key=None, algorithm="none"
    )
    request = Request(
        {"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]}
    )
    assert not is_native_session_request(request, "o.User.interactive")


@pytest.mark.parametrize("authorization", [b"", b"Bearer broken", b"Basic fake"])
def test_api_keys_and_client_actor_headers_are_not_session_proof(authorization):
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"authorization", authorization),
                (b"x-api-key", b"opaque-api-key"),
                (b"x-actor-kind", b"human"),
                (b"x-native-user", b"true"),
            ],
        }
    )
    request.state.user = {"id": "o.User.interactive", "api_key_id": "machine-key"}
    assert not is_native_session_request(request, "o.User.interactive")
