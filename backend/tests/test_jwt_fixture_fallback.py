"""Fallback test tokens use the production JWT library without obsolete jose."""

import builtins
from types import SimpleNamespace

import jwt
import pytest
from jvspatial.core.context import GraphContext
from jvspatial.db.factory import create_database

from app.config import settings
from app.services.authentication import IntegralAuthenticationService
from tests.conftest import jwt_for_second_user, jwt_for_test_user

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def no_jose(monkeypatch):
    original = builtins.__import__

    def imported(name, *args, **kwargs):
        if name == "jose" or name.startswith("jose."):
            raise ImportError("Obsolete JWT dependency is deliberately absent")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", imported)
    monkeypatch.setattr(settings, "SECRET_KEY", "synthetic-fixture-jwt-key-" * 3)
    monkeypatch.setattr(settings, "ALGORITHM", "HS256")


@pytest.mark.parametrize("fixture", [jwt_for_test_user, jwt_for_second_user])
async def test_fallback_token_interoperates_with_core_decoder(fixture, tmp_path):
    user = SimpleNamespace(user_id="synthetic-auth-user", id="synthetic-profile")
    token = await fixture.__wrapped__(user, None)
    assert isinstance(token, str) and token
    database = create_database(db_type="json", base_path=str(tmp_path / "graph"))
    service = IntegralAuthenticationService(
        GraphContext(database=database),
        jwt_secret=settings.SECRET_KEY,
        jwt_algorithm=settings.ALGORITHM,
    )
    claims = service._decode_jwt_token(token)
    assert claims["user_id"] == user.user_id
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


@pytest.mark.parametrize("fixture", [jwt_for_test_user, jwt_for_second_user])
async def test_live_token_is_preferred(fixture):
    assert await fixture.__wrapped__(None, "live-token") == "live-token"


@pytest.mark.parametrize("fixture", [jwt_for_test_user, jwt_for_second_user])
@pytest.mark.parametrize("user", [None, SimpleNamespace(user_id="", id="")])
async def test_absent_principal_does_not_invent_identity(fixture, user):
    assert await fixture.__wrapped__(user, None) is None
