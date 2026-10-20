"""frame-ancestors for package view documents."""

from app.services.app_extension_views import extension_frame_ancestors


def test_extension_frame_ancestors_allows_public_spa_beside_local_origin():
    policy = extension_frame_ancestors(
        "http://localhost:9006",
        "https://app.example.com",
    )
    assert policy == "'self' http://localhost:9006 https://app.example.com"


def test_extension_frame_ancestors_drops_path_and_duplicates():
    policy = extension_frame_ancestors(
        "http://localhost:9006/",
        "http://localhost:9006/oauth/authorize",
        "",
        "not-a-url",
    )
    assert policy == "'self' http://localhost:9006"
