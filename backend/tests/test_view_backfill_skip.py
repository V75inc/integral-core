"""The read-path view backfill is skipped only when it could not write anything."""

from types import SimpleNamespace

from app.services.operational_model_runtime import view_may_need_backfill


def _view(**kw):
    base = dict(
        entry_type_keys=["task"], default_entry_type_key="task", type="board", config={}
    )
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
    assert (
        view_may_need_backfill(
            _view(type="extension_view", config={"extension_view_key": "x"})
        )
        is False
    )


def test_non_dict_config_is_tolerated():
    assert view_may_need_backfill(_view(type="extension_view", config=None)) is True


import pytest

from app.services import operational_model_runtime as R


@pytest.mark.asyncio
async def test_seed_index_is_reused_within_ttl_and_rebuilt_after(monkeypatch):
    calls = {"n": 0}

    def fake_warm():
        calls["n"] += 1
        return {"k": {"entry_types": ["x"]}}

    monkeypatch.setattr(R, "warm_seeded_library_view_index", fake_warm)
    monkeypatch.setattr(R, "_SEED_INDEX_TTL_SECONDS", 60.0)
    monkeypatch.setattr(R, "_seed_index_cache", None)
    first = await R._warm_seeded_view_index_off_loop()
    second = await R._warm_seeded_view_index_off_loop()
    assert first == second and calls["n"] == 1
    # Expire the cached copy: the next call validates again.
    R._seed_index_cache = (0.0, first)
    await R._warm_seeded_view_index_off_loop()
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_seed_index_ttl_zero_never_caches(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(
        R,
        "warm_seeded_library_view_index",
        lambda: calls.__setitem__("n", calls["n"] + 1) or {},
    )
    monkeypatch.setattr(R, "_SEED_INDEX_TTL_SECONDS", 0.0)
    monkeypatch.setattr(R, "_seed_index_cache", None)
    await R._warm_seeded_view_index_off_loop()
    await R._warm_seeded_view_index_off_loop()
    assert calls["n"] == 2
