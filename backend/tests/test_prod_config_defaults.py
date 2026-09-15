"""Pins the config defaults that decide whether a deployment is in dev mode.

`DEBUG` is not cosmetic. `app/main.py` folds it into `_oauth_is_dev`, which
gates the OAuth issuer downgrade check -- the guard that keeps an `http://`
issuer from being stamped into token `iss`/`aud` and the discovery document --
and it selects uvicorn's `--reload`.

It previously defaulted to `True`, and `deploy/docker-stack.prod.yml` set
`JVSPATIAL_DEBUG` (a jvspatial variable this Settings class never reads) rather
than `DEBUG`. So `DEBUG` was unset in production and fell back to the insecure
default: the issuer check was skipped and reload was on. The default is the
backstop here -- the stack files are also fixed, but only the default protects
the *next* deployment that forgets.
"""

import re
from pathlib import Path

import pytest
import yaml

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEPLOY = REPO_ROOT / "deploy"
PROD_STACK = DEPLOY / "docker-stack.prod.yml"


def _api_env(stack_path: Path) -> dict:
    spec = yaml.safe_load(stack_path.read_text(encoding="utf-8"))
    return spec["services"]["api"].get("environment") or {}


class TestDebugDefault:
    def test_debug_defaults_to_false(self):
        """A deployment that never mentions DEBUG must not land in dev mode."""
        from app.config import Settings

        # Construct without the repo .env (which sets DEBUG=true for local dev)
        # so this asserts the *code* default, not the developer's environment.
        assert Settings.model_fields["DEBUG"].default is False

    def test_debug_is_what_gates_the_oauth_issuer_check(self):
        """Guards the coupling, so the default cannot be relaxed casually."""
        main_src = (REPO_ROOT / "backend" / "app" / "main.py").read_text(
            encoding="utf-8"
        )
        assert "_oauth_is_dev" in main_src
        assert re.search(r"_oauth_is_dev\s*=.*settings\.DEBUG", main_src, re.S), (
            "main.py no longer derives _oauth_is_dev from settings.DEBUG; "
            "re-point this test at whatever now gates the issuer check."
        )


@pytest.mark.skipif(not PROD_STACK.exists(), reason="prod stack file not present")
class TestProdStack:
    def test_sets_debug_explicitly_not_only_jvspatial_debug(self):
        """The original bug: right intent, wrong variable name.

        `JVSPATIAL_DEBUG` configures jvspatial. Integral's Settings reads
        `DEBUG`. Setting only the former is a silent no-op for this app.
        """
        env = _api_env(PROD_STACK)
        assert "DEBUG" in env, (
            "prod stack sets JVSPATIAL_DEBUG but not DEBUG; integral's Settings "
            "reads DEBUG, so debug mode would be decided by the code default"
        )
        assert str(env["DEBUG"]).strip().lower() in {"false", "0", "no"}

    def test_no_default_postgres_password(self):
        """`${VAR:-integral}` silently ships a password published in this repo."""
        raw = PROD_STACK.read_text(encoding="utf-8")
        bad = re.findall(r"POSTGRES_PASSWORD:\s*\$\{POSTGRES_PASSWORD:-[^}]*\}", raw)
        assert not bad, (
            "production must not default POSTGRES_PASSWORD; use "
            "${POSTGRES_PASSWORD:?...} so the deploy aborts when it is unset.\n  "
            + "\n  ".join(bad)
        )

    def test_postgres_password_is_required(self):
        raw = PROD_STACK.read_text(encoding="utf-8")
        assert raw.count("POSTGRES_PASSWORD:?") >= 1
