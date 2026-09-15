"""The boot guard must reject the placeholders our own env examples ship.

`deploy/.env.prod.example`, `.env.main.example` and `.env.dev.example` all set
`JVSPATIAL_JWT_SECRET_KEY=replace-with-openssl-rand-hex-32` — the alias that
feeds `Settings.SECRET_KEY`. That string is **exactly 32 characters**, so the
length check waved it through and it was not in the denylist: copy an example,
deploy it, and the service boots signing JWTs with a value published in this
repository.

The guard is a module-level `sys.exit(1)` in `app/main.py`, which cannot be
triggered from a test without killing the run (an import-time exit under xdist
surfaces as `INTERNALERROR … KeyError: <WorkerController gwN>`, not a readable
failure — see the CI/local divergence note in CLAUDE.md). So these exercise the
predicate directly and pin the example files against it, which is the pairing
that actually breaks: someone adds a new example with a new placeholder, and
nothing tells them the guard does not know about it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Imported at module scope, deliberately. Importing ``app.main`` populates
# settings-derived environment defaults (REGISTRATION_OPEN,
# PERMISSION_PROCESS_CACHE_TTL, …) as a one-time side effect; doing it inside a
# test puts that mutation inside the window the env-leak guard watches, and the
# guard correctly flags it. At module scope it happens during collection, where
# it belongs.
from app.main import _secret_key_is_acceptable  # noqa: E402

pytestmark = pytest.mark.smoke

# Templates someone can copy toward a real deployment. Every one of these MUST
# be refused by the boot guard.
_DEPLOYABLE_EXAMPLES = (
    "deploy/.env.prod.example",
    "deploy/.env.main.example",
    "deploy/.env.dev.example",
    ".env.example",
)

# The documented exception, and the reason it is one.
#
# `docker-compose.local.yml:62` hardcodes this same value as an inline fallback
# so `docker compose -f docker-compose.local.yml up` works with no `.env` at
# all — that is the quickstart. Adding `local-dev` to the marker list would
# reject it and break that path for everyone, so it stays accepted.
#
# It is still a signing key published in this repo, so the guard rails are:
# it may only appear in the local-compose pair, and it must never reach a
# deployable template. `test_the_local_compose_key_stays_local` is what
# enforces that, and it is the test that fails if someone pastes this value
# into `deploy/`.
_LOCAL_ONLY_EXAMPLE = ".env.docker.example"
_LOCAL_ONLY_COMPOSE = "docker-compose.local.yml"

# Globs rather than a list: every stack/compose file except the local-only one
# is somewhere the key could be pasted as a literal, and a new stack file
# should be covered the day it lands rather than the day someone remembers.
# They interpolate ``${JVSPATIAL_JWT_SECRET_KEY}`` today, which is the point —
# there is nothing to find until someone inlines a value.
_DEPLOYABLE_STACK_GLOBS = ("deploy/*.yml", "docker-compose*.yml")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _secret_from(example: Path) -> str | None:
    for line in example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("JVSPATIAL_JWT_SECRET_KEY="):
            return stripped.split("=", 1)[1]
    return None


def test_a_real_random_secret_is_accepted():
    # `openssl rand -hex 32` shape — the thing operators are told to use.
    assert _secret_key_is_acceptable("a" * 64) is True
    assert (
        _secret_key_is_acceptable(
            "9f2b7c1e4d5a6b8c0e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f"
        )
        is True
    )


def test_short_secrets_are_rejected():
    assert _secret_key_is_acceptable("") is False
    assert _secret_key_is_acceptable("a" * 31) is False


@pytest.mark.parametrize("example", _DEPLOYABLE_EXAMPLES)
def test_every_deployable_example_placeholder_is_rejected(example: str):
    """The load-bearing case: our own deployment templates must not boot.

    Parameterised over the files rather than the strings so a new example, or a
    changed placeholder, is covered without anyone remembering to update a list
    here.
    """
    path = _repo_root() / example
    assert path.exists(), (
        f"{example} is gone — either restore it or drop it from "
        "_DEPLOYABLE_EXAMPLES. A silent skip here would retire the guard "
        "without anyone deciding to."
    )
    secret = _secret_from(path)
    assert secret is not None, (
        f"{example} no longer sets JVSPATIAL_JWT_SECRET_KEY. Deleting the line "
        "falls back to the config default (which the guard does reject), but "
        "the template stops showing operators what to set — decide that "
        "deliberately rather than by omission."
    )

    assert _secret_key_is_acceptable(secret) is False, (
        f"{example} ships JVSPATIAL_JWT_SECRET_KEY={secret!r}, which the boot "
        "guard accepts — copying that file into a deployment would sign tokens "
        "with a key published in this repo"
    )


def test_the_local_compose_key_stays_local():
    """The one published key the guard accepts, fenced to where it belongs.

    `.env.docker.example` and `docker-compose.local.yml` share a hardcoded
    fallback so the compose quickstart runs with no `.env`. It is >= 32 chars
    and carries no placeholder wording, so the guard lets it boot — deliberate,
    because rejecting it would break the documented local path.

    What must never happen is that value reaching something deployable. If this
    fails, someone pasted the local key into a deployment template.
    """
    local_secret = _secret_from(_repo_root() / _LOCAL_ONLY_EXAMPLE)
    assert local_secret is not None, (
        f"{_LOCAL_ONLY_EXAMPLE} no longer sets JVSPATIAL_JWT_SECRET_KEY; "
        "update this test to match whatever the local quickstart now does"
    )
    # Documented exception — asserted, so the exception is visible rather than
    # implied by the absence of a test.
    assert _secret_key_is_acceptable(local_secret) is True

    compose = (_repo_root() / _LOCAL_ONLY_COMPOSE).read_text(encoding="utf-8")
    assert local_secret in compose, (
        f"{_LOCAL_ONLY_EXAMPLE} and {_LOCAL_ONLY_COMPOSE} have drifted apart; "
        "the example is only safe because it matches the compose fallback"
    )

    scanned = []
    for deployable in _DEPLOYABLE_EXAMPLES:
        path = _repo_root() / deployable
        if path.exists():
            scanned.append(path)
    for glob in _DEPLOYABLE_STACK_GLOBS:
        for path in sorted(_repo_root().glob(glob)):
            if path.name == _LOCAL_ONLY_COMPOSE:
                continue
            scanned.append(path)

    assert scanned, "found nothing to scan — the fence would pass vacuously"

    for path in scanned:
        rel = path.relative_to(_repo_root())
        assert local_secret not in path.read_text(encoding="utf-8"), (
            f"{rel} contains the local-compose signing key. That value is "
            "published in this repo AND passes the boot guard, so a deployment "
            "built from it would sign tokens with a key anyone can read. Stack "
            "files should interpolate ${JVSPATIAL_JWT_SECRET_KEY}, never inline "
            "a literal."
        )


def test_placeholder_wording_is_rejected_even_at_full_length():
    """Length is not the signal; intent is.

    Each of these is >= 32 characters and would have passed before.
    """
    for candidate in (
        "replace-with-openssl-rand-hex-32",
        "replace-me-openssl-rand-hex-32-______________________",
        "CHANGE-ME-CHANGE-ME-CHANGE-ME-CHANGE-ME",
        "your-secret-key-goes-right-here-ok",
        "placeholder-placeholder-placeholder",
    ):
        assert _secret_key_is_acceptable(candidate) is False, candidate
