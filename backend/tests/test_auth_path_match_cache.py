"""Cached route-pattern matching must agree with the library's for every pattern shape."""

import pytest

from jvspatial.api.components import endpoint_auth_resolver as lib

from app.services.jvspatial_auth_fast import path_matches_cached

PATTERNS = [
    "/", "", "/apps", "/apps/{app_id}", "/apps/{app_id}/tracks", "/tracks/{track_id}/detail",
    "/files/{path:path}", "/files/{path:path}/meta", "/x/{n:int}", "/a.b/{id}", "/e+x/{id}",
    "/auth/ws-ticket", "/health",
]
PATHS = [
    "/", "/apps", "/apps/", "/apps/n.WorkspaceApp.1", "/apps/n.WorkspaceApp.1/tracks",
    "/apps/a/b/tracks", "/tracks/n.Track.1/detail", "/files/a/b/c", "/files/a/b/c/meta",
    "/x/12", "/a.b/z", "/aXb/z", "/e+x/q", "/eex/q", "/auth/ws-ticket", "/health", "/healthz", "/nope", "",
]


@pytest.mark.parametrize("pattern", PATTERNS)
def test_cached_matching_equals_the_library(pattern):
    for path in PATHS:
        assert path_matches_cached(pattern, path) == lib._path_matches(pattern, path), (pattern, path)


from types import SimpleNamespace as NS

from app.services.jvspatial_auth_fast import endpoint_info_matches_method_fast


@pytest.mark.parametrize("methods", [None, [], ["GET"], ["get"], ["GET", "POST"], ["post", "Put"], ("DELETE",)])
@pytest.mark.parametrize("request_method", [None, "", "GET", "get", "POST", "put", "PATCH", "DELETE"])
def test_method_matching_equals_the_library(methods, request_method):
    info = NS(methods=methods)
    assert endpoint_info_matches_method_fast(info, request_method) == lib._endpoint_info_matches_method(info, request_method)


def test_endpoint_without_methods_attribute():
    assert endpoint_info_matches_method_fast(object(), "GET") == lib._endpoint_info_matches_method(object(), "GET")
