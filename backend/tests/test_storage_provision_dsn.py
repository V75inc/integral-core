"""Runtime credentials must not change a provisioned PostgreSQL endpoint."""

from urllib.parse import unquote, urlsplit

import pytest

from app.services.storage_indexes.provision import serving_dsn


@pytest.mark.parametrize(
    "source",
    [
        "postgresql://postgres@/postgres?host=/tmp/private-pg&port=5544",
        "postgresql:///postgres?host=%2Ftmp%2Fprivate-pg&options=-c%20statement_timeout%3D1000",
        "postgresql://migration:old@db.example:5544/tenant?sslmode=require",
        "postgresql://migration@db.example/tenant",
        "postgresql://migration:old@[::1]:5544/tenant?application_name=test",
        "postgresql://migration@[2001:db8::1]/tenant",
    ],
)
def test_serving_dsn_changes_only_encoded_userinfo(source):
    role, password = "runtime@role", "synthetic:@/ ?#% secret"
    result = serving_dsn(source, role, password)
    before, after = urlsplit(source), urlsplit(result)
    assert after.netloc.rsplit("@", 1)[-1] == before.netloc.rsplit("@", 1)[-1]
    assert (after.scheme, after.path, after.query, after.fragment) == (
        before.scheme,
        before.path,
        before.query,
        before.fragment,
    )
    assert unquote(after.username) == role
    assert unquote(after.password) == password


def test_serving_socket_dsn_does_not_introduce_tcp_endpoint():
    source = "postgresql://postgres@/postgres?host=/tmp/private-pg"
    result = urlsplit(serving_dsn(source, "runtime", "synthetic-password"))
    assert result.hostname is None
    assert result.port is None
    assert result.query == "host=/tmp/private-pg"
