"""An App can hide a track's search box and Filters (``defaults.toolbar``); default is shown."""

import asyncio
from types import SimpleNamespace

import pytest

from app.api.errors import BadRequestError
from app.services import entry_context
from app.services.operational_model_compile import (
    _normalize_track_defaults,
    normalize_track_toolbar,
)


def test_toolbar_option_is_validated_and_normalized():
    assert normalize_track_toolbar(None, where="t") == {}
    assert normalize_track_toolbar({}, where="t") == {}
    assert normalize_track_toolbar({"show_search_and_filters": False}, where="t") == {
        "show_search_and_filters": False
    }
    for bad in ({"show_search_and_filters": "no"}, {"show_search": False}, "x"):
        with pytest.raises(BadRequestError):
            normalize_track_toolbar(bad, where="t")


def test_defaults_carry_toolbar_next_to_tabs_and_leave_others_alone():
    defaults = {"default_view": "feed"}
    assert _normalize_track_defaults(defaults, where="d") == defaults
    assert _normalize_track_defaults(
        {
            "tabs": {"show_single_tab": False},
            "toolbar": {"show_search_and_filters": False},
        },
        where="d",
    ) == {
        "tabs": {"show_single_tab": False},
        "toolbar": {"show_search_and_filters": False},
    }


@pytest.mark.parametrize(
    "tier_defaults, expected",
    [
        (
            {"toolbar": {"show_search_and_filters": False}},
            {"show_search_and_filters": False},
        ),
        (
            {"toolbar": {"show_search_and_filters": True}},
            {"show_search_and_filters": True},
        ),
        ({"toolbar": {}}, None),
        ({}, None),
    ],
)
def test_track_payload_projects_only_the_supported_toolbar_option(
    monkeypatch, tier_defaults, expected
):
    async def resolve(_track):
        return None, {"defaults": tier_defaults}, None

    monkeypatch.setattr(entry_context, "resolve_track_runtime_profile", resolve)
    out = asyncio.run(
        entry_context.attach_operational_model_defaults_to_track_data(
            {}, SimpleNamespace()
        )
    )
    got = (out.get("operational_model_defaults") or {}).get("toolbar")
    assert got == expected
