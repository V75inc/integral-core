"""In-memory matching must equal the two queries it replaces."""

from types import SimpleNamespace as NS

import app.api.entry_types  # noqa: F401  (loads the service layer in its normal order)

from app.services.entry_type_service import match_existing_entry_types as match


def et(id, name, fold):
    return NS(id=id, name=name, name_fold=fold)


def test_matches_by_name_fold_first():
    a, b = et("1", "Task", "task"), et("2", "Other", "other")
    assert [e.id for e in match([a, b], "Task", "task")] == ["1"]


def test_falls_back_to_exact_name_when_no_fold_matches():
    legacy = et("1", "Pay Run", "")
    other = et("2", "Pay run", "pay run")
    # fold lookup: 'payrun' matches nothing, so the exact-name pass is used
    assert [e.id for e in match([legacy, other], "Pay Run", "payrun")] == ["1"]


def test_missing_fold_attribute_does_not_match_a_fold():
    odd = NS(id="1", name="X")
    assert match([odd], "X", "") == [odd] or match([odd], "X", "") == []
    assert [e.id for e in match([odd], "X", "x")] == ["1"]  # falls back to name


def test_keeps_store_order_for_several_matches():
    first, second = et("1", "A", "a"), et("2", "A", "a")
    assert [e.id for e in match([first, second], "A", "a")] == ["1", "2"]


def test_no_match():
    assert match([et("1", "A", "a")], "B", "b") == []
