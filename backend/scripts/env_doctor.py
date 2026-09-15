"""Report where each setting actually came from.

"My .env has no effect" is nearly always precedence, not a broken file:
config.py loads backend/.env FIRST and root .env second with
``override=False``, so the effective order is

    real shell environment  >  backend/.env  >  <repo>/.env

An exported shell variable therefore beats BOTH files, and a stale
backend/.env silently shadows the root one. This prints the winner and why.

    python scripts/env_doctor.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent

# Captured before app.config imports dotenv and mutates os.environ.
_PRE_EXISTING = set(os.environ)


def _keys(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.isidentifier() or k.replace("_", "").isalnum():
            out[k] = v.strip()
    return out


def main() -> int:
    backend_env = BACKEND / ".env"
    root_env = ROOT / ".env"

    print("env files (loaded in this order; first to set a key wins):")
    # backend/.env is OPTIONAL and absent in a healthy setup — its only job is
    # per-machine overrides, and when it exists it shadows the root file.
    print(
        f"  {'present' if backend_env.is_file() else 'absent '} "
        f"{backend_env}"
        f"{'   <- shadows the root file' if backend_env.is_file() else '   (optional, normal)'}"
    )
    print(
        f"  {'present' if root_env.is_file() else 'MISSING'} "
        f"{root_env}"
        f"{'   <- your settings belong here' if root_env.is_file() else '   <- REQUIRED'}"
    )
    if not root_env.is_file():
        print("\n  No root .env. Copy the template:")
        print(f"    cp {ROOT / '.env.example'} {root_env}")

    b, r = _keys(backend_env), _keys(root_env)

    shadowed = sorted(set(b) & set(r))
    if shadowed:
        print("\nSHADOWED — backend/.env wins, your root .env edit does nothing:")
        for k in shadowed:
            print(f"  {k}")

    from_shell = sorted((set(b) | set(r)) & _PRE_EXISTING)
    if from_shell:
        print("\nOVERRIDDEN BY SHELL — exported vars beat BOTH .env files:")
        for k in from_shell:
            print(f"  {k}   (unset it, or edit the export, not .env)")

    missing = sorted(set(_keys(ROOT / ".env.example")) - set(b) - set(r))
    if missing:
        print("\nIn .env.example but not in your .env (defaults apply):")
        for k in missing:
            print(f"  {k}")

    sys.path.insert(0, str(BACKEND))
    from app.config import settings  # noqa: E402  (after env capture)

    print("\nresolved values:")
    for name in (
        "DEBUG",
        "JVSPATIAL_DB_TYPE",
        "JVAGENT_UPDATE_MODE",
        "INTEGRAL_AGENT_KEY_MODE",
        "INTEGRAL_AGENT_TURN_TIMEOUT_SECONDS",
    ):
        # Not every var is a Settings field — jvspatial reads some (e.g.
        # JVSPATIAL_DB_TYPE) straight from the environment.
        if hasattr(settings, name):
            val = getattr(settings, name)
        else:
            val = os.environ.get(name, "<unset>")
        src = (
            "shell"
            if name in _PRE_EXISTING
            else (
                "backend/.env"
                if name in b
                else ("root .env" if name in r else "default")
            )
        )
        print(f"  {name} = {val!r}   [{src}]")

    try:
        from importlib.metadata import version

        print(f"\njvagent {version('jvagent')} | jvspatial {version('jvspatial')}")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
