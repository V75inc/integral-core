"""_fast_copy must be an independent, equal copy and fall back for non-JSON types."""

import copy
from collections import OrderedDict

from app.services.operational_model_compile import _fast_copy


def test_nested_json_data_is_equal_and_independent():
    src = {"a": [1, 2.5, "x", None, True, {"b": [{"c": "d"}]}], "e": {"f": []}}
    out = _fast_copy(src)
    assert out == src and out == copy.deepcopy(src)
    out["a"][5]["b"][0]["c"] = "changed"
    out["e"]["f"].append(1)
    assert src["a"][5]["b"][0]["c"] == "d" and src["e"]["f"] == []


def test_types_are_preserved_and_non_json_types_fall_back():
    src = {"t": (1, [2]), "s": {1, 2}, "o": OrderedDict(a=[1]), "l": [(3,)]}
    out = _fast_copy(src)
    assert out == src
    assert (
        type(out["t"]) is tuple
        and type(out["s"]) is set
        and type(out["o"]) is OrderedDict
    )
    out["t"][1].append(9)
    out["o"]["a"].append(9)
    assert src["t"][1] == [2] and src["o"]["a"] == [1]


def test_scalars_and_empty_containers():
    for v in (None, 1, 1.5, "s", True, {}, []):
        assert _fast_copy(v) == v
