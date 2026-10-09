"""The deploy files must agree with each other — drift here ships silently.

Every invariant in this file was a live inconsistency found in review, and
each one shared a failure shape: nothing errors, healthchecks stay green, and
the divergence surfaces weeks later as behaviour nobody can explain.

2. ``WEB_CONCURRENCY``: >1 uvicorn worker breaks chat invariants (per-process
   turn registry + WS fan-out, ADR-005) and the boot check only WARNS. The
   stacks pin the literal ``"1"`` and the api-env overlay denies the knob,
   because the overlay is passed AFTER the stack file and would win.

3. The prod SPLIT-HOST trap: `docker-stack.prod.yml` routes the API on
   ``Host(`${DOMAIN_API}`)``, the SPA nginx is static-only, and the example
   used to show only the same-origin form — mis-copy it and every XHR dies
   while healthchecks stay green. The example must name ``DOMAIN_API``.

Parsed textually, not with a YAML library: the stack files are envsubst-style
templates whose ``${VAR:-default}`` forms are exactly the thing under test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

_REPO = Path(__file__).resolve().parents[2]

_DEPLOYABLE_STACKS = (
    "deploy/docker-stack.prod.yml",
    "deploy/docker-stack.main.yml",
    "deploy/docker-stack.dev.yml",
    "deploy/docker-compose.prod.yml",
)


def _read(rel: str) -> str:
    path = _REPO / rel
    assert path.exists(), f"{rel} is gone — update this test with the rename"
    return path.read_text(encoding="utf-8")


def _example_value(rel: str, key: str) -> str | None:
    for line in _read(rel).splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            return stripped.split("=", 1)[1].strip()
    return None


def _stack_fallback(rel: str, key: str) -> str | None:
    # matches  KEY: ${KEY:-fallback}
    m = re.search(rf"{key}: \$\{{{key}:-([a-z0-9_]+)\}}", _read(rel))
    return m.group(1) if m else None


@pytest.mark.parametrize("stack", _DEPLOYABLE_STACKS)
def test_web_concurrency_is_pinned_literal(stack):
    content = _read(stack)
    assert 'WEB_CONCURRENCY: "1"' in content, (
        f'{stack} no longer pins WEB_CONCURRENCY to the literal "1". '
        "More than one uvicorn worker breaks the per-process turn registry "
        "and WS fan-out (ADR-005) and the boot check only warns. If ADR-005 "
        "has been resolved and workers can scale, update this test alongside "
        "the ADR — do not just delete the pin."
    )
    assert "${WEB_CONCURRENCY" not in content, (
        f"{stack} interpolates WEB_CONCURRENCY from the environment — that "
        "reintroduces the env-file override path the pin exists to close"
    )


def test_overlay_denies_worker_and_update_mode_knobs():
    deny = re.search(
        r"DENY_RE='([^']+)'", _read("deploy/ci-generate-api-env-overlay.sh")
    )
    assert deny, "DENY_RE not found in ci-generate-api-env-overlay.sh"
    pattern = deny.group(1)
    for key in ("WEB_CONCURRENCY", "WORKERS"):
        assert re.fullmatch(pattern, key), (
            f"the api-env overlay no longer denies {key}. The overlay compose "
            "file is listed AFTER the stack file at `docker stack deploy`, so "
            "an env-file value for this key would silently beat the stack's "
            "own setting."
        )


def test_prod_split_host_trap_is_documented_where_it_bites():
    stack = _read("deploy/docker-stack.prod.yml")
    assert "Host(`${DOMAIN_API}`)" in stack, (
        "docker-stack.prod.yml no longer routes the API on DOMAIN_API — "
        "rewrite this test (and .env.prod.example's routing block) for the "
        "new topology rather than deleting it"
    )
    # The file's own header must not describe same-origin routing while the
    # router rule is split-host — that lie is what made the example look fine.
    header = "\n".join(stack.splitlines()[:12])
    assert "SPLIT-HOST" in header, (
        "docker-stack.prod.yml's header no longer states the split-host "
        "topology; the header is the first thing an operator reads"
    )
    example = _read("deploy/.env.prod.example")
    assert "DOMAIN_API" in example, (
        ".env.prod.example never mentions DOMAIN_API, but the prod stack "
        "requires it — copying the example against docker-stack.prod.yml "
        "yields an SPA calling a static nginx that cannot proxy, with green "
        "healthchecks throughout"
    )
