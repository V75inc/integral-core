"""Self-only time-zone persistence and user-local reminder semantics."""

import asyncio

import pytest

from app.agentive.services.routine_tasks import parse_run_at
from app.api.errors import BadRequestError
from app.schemas.user_timezone import UserTimezoneUpdate
from app.services.user_timezone import (
    get_user_timezone,
    set_user_timezone,
    user_time_context,
)

pytestmark = [pytest.mark.smoke]


@pytest.mark.asyncio
async def test_timezone_roundtrip_and_inference_preserves_manual_choice(
    authenticated_client, test_user
):
    endpoint = "/api/users/me/timezone"
    initial = await authenticated_client.get(endpoint)
    assert initial.status_code == 200
    assert initial.json() == {"timezone": "UTC", "configured": False}
    inferred = await authenticated_client.patch(
        endpoint, json={"timezone": "America/Guyana", "only_if_unset": True}
    )
    assert inferred.status_code == 200, inferred.text
    assert inferred.json()["timezone"] == "America/Guyana"
    manual = await authenticated_client.patch(endpoint, json={"timezone": "Asia/Tokyo"})
    assert manual.status_code == 200, manual.text
    ignored = await authenticated_client.patch(
        endpoint, json={"timezone": "America/New_York", "only_if_unset": True}
    )
    assert ignored.json()["timezone"] == "Asia/Tokyo"
    assert await get_user_timezone(test_user.user_id or test_user.id) == "Asia/Tokyo"
    profile = (await authenticated_client.get("/api/auth/me")).json()
    assert "Asia/Tokyo" in str(profile)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"timezone": "Not/AZone"},
        {"timezone": ""},
        {"timezone": "UTC", "user_id": "someone-else"},
        {"timezone": "../UTC"},
    ],
)
async def test_invalid_zone_and_target_override_rejected(
    authenticated_client, test_user, payload
):
    result = await authenticated_client.patch("/api/users/me/timezone", json=payload)
    assert result.status_code == 400, result.text
    assert await get_user_timezone(test_user.user_id or test_user.id) == "UTC"


@pytest.mark.asyncio
async def test_inference_race_never_overwrites_manual_choice(test_user):
    principal = test_user.user_id or test_user.id
    await asyncio.gather(
        set_user_timezone(
            principal, UserTimezoneUpdate(timezone="America/Guyana", only_if_unset=True)
        ),
        set_user_timezone(principal, UserTimezoneUpdate(timezone="Asia/Tokyo")),
    )
    assert await get_user_timezone(principal) == "Asia/Tokyo"
    assert "Account time zone (server-verified): Asia/Tokyo" in await user_time_context(
        principal
    )


@pytest.mark.asyncio
async def test_timezone_requires_auth(client):
    for method in (client.get, client.patch):
        response = await method("/api/users/me/timezone")
        assert response.status_code in (401, 403)


def test_local_reminder_and_absolute_offset():
    assert (
        parse_run_at("2099-01-15T09:00:00", "America/Guyana")
        == "2099-01-15T13:00:00+00:00"
    )
    assert (
        parse_run_at("2099-01-15T09:00:00+09:00", "America/Guyana")
        == "2099-01-15T00:00:00+00:00"
    )


@pytest.mark.parametrize("date", ["2099-03-08T02:30:00", "2099-11-01T01:30:00"])
def test_dst_gap_and_fold_require_explicit_offset(date):
    with pytest.raises(BadRequestError, match="ambiguous or does not exist"):
        parse_run_at(date, "America/New_York")
