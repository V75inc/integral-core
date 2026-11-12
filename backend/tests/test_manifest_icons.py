"""Apps and Tracks name an icon from Integral's fixed list; anything else is refused when the manifest compiles."""

import re
from pathlib import Path

import pytest

from app.api.errors import BadRequestError
from app.services.manifest_icons import MANIFEST_ICON_KEYS, normalize_manifest_icon
from app.services.operational_model_compile import compile_canonical_manifest

FRONTEND_LIST = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "lib"
    / "manifestLucideIcon.ts"
)


def _manifest(app_icon="", track_icon=""):
    app = {
        "tracks": [
            {
                "key": "records",
                "name": "Records",
                **({"icon": track_icon} if track_icon else {}),
                "entry_types": [{"key": "record", "name": "Record", "fields": []}],
            }
        ]
    }
    if app_icon:
        app["icon"] = app_icon
    return {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {"name": "Icon Test", "slug": "icon-test", "version": "1.0.0"},
        "app": app,
    }


def test_app_and_track_icons_pass_through_when_in_the_list():
    out = compile_canonical_manifest(manifest=_manifest("banknote", "receipt"))
    assert out["app"]["icon"] == "banknote"
    assert out["app"]["tracks"][0]["icon"] == "receipt"


def test_no_icon_leaves_the_key_out():
    out = compile_canonical_manifest(manifest=_manifest())
    assert "icon" not in out["app"]
    assert "icon" not in out["app"]["tracks"][0]


def test_a_name_outside_the_list_is_refused_and_the_message_lists_valid_ones():
    with pytest.raises(BadRequestError) as err:
        compile_canonical_manifest(manifest=_manifest(track_icon="not-an-icon"))
    assert "not-an-icon" in err.value.message and "banknote" in err.value.message
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(
            manifest=_manifest(app_icon="https://example.com/x.png")
        )


def test_names_are_normalised():
    assert normalize_manifest_icon("Dollar_Sign", where="x") == "dollar-sign"


def test_backend_list_matches_the_frontend_list():
    if not FRONTEND_LIST.is_file():
        pytest.skip("frontend sources are not in this checkout")
    text = FRONTEND_LIST.read_text()
    start = text.index("const MANIFEST_ICON_BY_ID")
    body = text[start : text.index("};", start)]
    names = set(re.findall(r"^\s+'?([a-z0-9-]+)'?: [A-Za-z0-9]+,$", body, flags=re.M))
    assert names == set(MANIFEST_ICON_KEYS)
