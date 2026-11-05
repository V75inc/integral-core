"""The read-path view backfill is skipped only when it could not write anything."""

from types import SimpleNamespace

from app.services.operational_model_runtime import view_may_need_backfill


def _view(**kw):
    base = dict(entry_type_keys=["task"], default_entry_type_key="task", type="board", config={})
    base.update(kw)
    return SimpleNamespace(**base)


def test_complete_view_needs_nothing():
    assert view_may_need_backfill(_view()) is False


def test_missing_entry_type_keys_needs_backfill():
    assert view_may_need_backfill(_view(entry_type_keys=[])) is True
    assert view_may_need_backfill(_view(entry_type_keys=None)) is True


def test_missing_default_entry_type_needs_backfill():
    assert view_may_need_backfill(_view(default_entry_type_key="")) is True


def test_extension_view_without_key_needs_backfill():
    assert view_may_need_backfill(_view(type="extension_view")) is True
    assert view_may_need_backfill(_view(type="extension_view", config={"extension_view_key": "x"})) is False


def test_non_dict_config_is_tolerated():
    assert view_may_need_backfill(_view(type="extension_view", config=None)) is True
