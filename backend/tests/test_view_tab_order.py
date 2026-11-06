"""An App declares its default tab order; a person's own order survives an App update."""

from app.services.operational_model_compile import (
    default_tab_order_for,
    keep_person_tab_order,
    normalize_view_config,
)


def test_a_view_defaults_to_its_place_in_the_manifest():
    assert default_tab_order_for({"key": "a"}, 0) == 0
    assert default_tab_order_for({"key": "c"}, 2) == 2


def test_a_declared_tab_order_wins_over_the_place_in_the_manifest():
    assert default_tab_order_for({"key": "a", "tab_order": 7}, 0) == 7


def test_a_non_integer_declared_order_is_ignored():
    assert default_tab_order_for({"key": "a", "tab_order": "first"}, 3) == 3
    assert default_tab_order_for({"key": "a", "tab_order": True}, 3) == 3


def test_the_default_order_is_kept_by_the_config_contract():
    assert (
        normalize_view_config("feed", {"default_tab_order": 4})["default_tab_order"]
        == 4
    )


def test_a_bad_default_order_is_refused():
    import pytest

    from app.api.errors import BadRequestError

    with pytest.raises(BadRequestError):
        normalize_view_config("feed", {"default_tab_order": "x"})


def test_an_app_update_keeps_the_order_a_person_chose():
    old = {"tab_order": 5, "default_tab_order": 1}
    new = {"default_tab_order": 0}
    assert keep_person_tab_order(old, new) == {"default_tab_order": 0, "tab_order": 5}


def test_an_update_adds_no_person_order_when_there_was_none():
    assert keep_person_tab_order(
        {"default_tab_order": 1}, {"default_tab_order": 0}
    ) == {"default_tab_order": 0}
    assert keep_person_tab_order(None, {"default_tab_order": 0}) == {
        "default_tab_order": 0
    }


def test_track_tabs_are_validated_and_normalized():
    import pytest

    from app.api.errors import BadRequestError
    from app.services.operational_model_compile import normalize_track_tabs

    assert normalize_track_tabs(None, where="t") == {}
    assert normalize_track_tabs(
        {"order": ["Start Here", "rates"], "show_single_tab": False}, where="t"
    ) == {
        "order": ["start_here", "rates"],
        "show_single_tab": False,
    }
    for bad in (
        {"order": "a"},
        {"order": [1]},
        {"order": ["a", "a"]},
        {"show_single_tab": "no"},
        {"placement": "side"},
        "x",
    ):
        with pytest.raises(BadRequestError):
            normalize_track_tabs(bad, where="t")


def test_an_app_without_tab_options_is_untouched():
    from app.services.operational_model_compile import _normalize_track_defaults

    defaults = {"default_view": "feed", "default_entry_type": "note"}
    assert _normalize_track_defaults(defaults, where="d") == defaults
    assert _normalize_track_defaults({"tabs": {"order": ["a"]}}, where="d") == {
        "tabs": {"order": ["a"]}
    }
