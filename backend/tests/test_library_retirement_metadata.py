"""Tests for generic package retirement metadata."""

from types import SimpleNamespace

from app.api.operational_models import _is_visible_library_profile


def test_library_retirement_is_independent_of_package_identity():
    profile = SimpleNamespace(
        name="Archived example",
        metadata={"slug": "team-example", "library_retired": True},
        manifest={"package": {"name": "team-example"}},
    )
    assert not _is_visible_library_profile(profile)
    profile.metadata["library_retired"] = False
    assert _is_visible_library_profile(profile)
