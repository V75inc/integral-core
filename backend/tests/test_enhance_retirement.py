"""Retired enhancement services cannot be invoked outside the removed UI."""

import importlib.util

from app.services import operational_model_authoring


def test_enhance_service_and_recommendation_entrypoint_are_absent():
    assert importlib.util.find_spec("app.services.improve_this") is None
    assert not hasattr(operational_model_authoring, "recommend_profile_customizations")
