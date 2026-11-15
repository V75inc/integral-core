"""Reviewed offline upgrades for owned native desktop storage.

Plans are reviewed offline; the updater applies an exact approved file only after
its cold snapshot. Credentials remain in the private home, never in plan/output.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

CONTRACT_VERSION = 1


class NativeUpgradeError(ValueError):
    """A bounded maintenance error safe to display without connection details."""


def digest(value: dict[str, Any]) -> str:
    """Hash canonical configuration without retaining plaintext in a plan."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_private(path: Path) -> dict[str, Any]:
    """Read a bounded regular control file without following a linked leaf."""
    if linked(path) or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise NativeUpgradeError("Expected a regular bounded maintenance file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise NativeUpgradeError("Expected a maintenance object")
    return value


def linked(path: Path) -> bool:
    """Reject Windows directory junctions as well as ordinary symbolic links."""
    return path.is_symlink() or getattr(os.path, "isjunction", lambda _: False)(path)


def write_private(path: Path, value: dict[str, Any]) -> None:
    """Atomically retain private metadata without replacing linked paths."""
    from app.local_runtime import private_json

    temporary = path.with_suffix(".tmp")
    if linked(path) or temporary.exists() or linked(temporary):
        raise NativeUpgradeError("Unsafe maintenance destination")
    private_json(path, value)


def config_for_plan(home: Path) -> dict[str, Any]:
    """Accept an existing desktop home that predates serving-role adoption."""
    config = read_private(home / "installation.json")
    if (
        config.get("contract_version") != 1
        or config.get("deployment_profile") != "desktop_single_user"
    ):
        raise NativeUpgradeError("Only an existing desktop installation may migrate")
    if any(k in config for k in ("runtime_database_user", "runtime_database_password")):
        raise NativeUpgradeError(
            "Runtime credentials already exist; use a separately reviewed upgrade"
        )
    if not config.get("installation_id"):
        raise NativeUpgradeError("Missing installation identity")
    return config


async def prepare(
    conn: Any, home: Path, wheel_hash: str, budgets: dict[str, int]
) -> dict[str, Any]:
    """Bind a read-only catalog plan to this home and the selected wheel."""
    from app.services.storage_indexes.catalog import build_catalog
    from app.services.storage_indexes.lifecycle import plan

    config = config_for_plan(home)
    include_search = bool(
        await conn.fetchval("SELECT 1 FROM pg_available_extensions WHERE name='vector'")
    )
    specs = build_catalog(include_search=include_search)
    role = "integral_runtime_" + secrets.token_hex(8)
    artifact = await plan(
        conn, specs, runtime_role=role, include_search=include_search, **budgets
    )
    return {
        "version": 1,
        "home": str(home.resolve()),
        "installation_id": config["installation_id"],
        "installation_sha256": digest(config),
        "wheel_sha256": wheel_hash,
        "plan": artifact,
    }


def validate(envelope: dict[str, Any], home: Path, wheel_hash: str) -> dict[str, Any]:
    """Reject destructive operations, another home, or changed installation keys."""
    from app.services.storage_indexes.lifecycle import validate_plan

    if (
        envelope.get("version") != 1
        or envelope.get("home") != str(home.resolve())
        or envelope.get("wheel_sha256") != wheel_hash
    ):
        raise NativeUpgradeError("Migration belongs to another home or runtime")
    plan = envelope["plan"]
    validate_plan(plan)
    if plan["target"]["schema"] != "public" or not re.fullmatch(
        r"integral_runtime_[a-f0-9]{16}", plan.get("runtime_role", "")
    ):
        raise NativeUpgradeError("Invalid local runtime role or schema")
    if any(
        op["kind"] not in {"schema", "create", "register"} for op in plan["operations"]
    ):
        raise NativeUpgradeError(
            "Native upgrade refuses destructive or repair operations"
        )
    config = read_private(home / "installation.json")
    if (
        sum(
            key in config
            for key in ("runtime_database_user", "runtime_database_password")
        )
        == 1
    ):
        raise NativeUpgradeError(
            "Incomplete runtime credentials require recovery review"
        )
    original = {
        k: v
        for k, v in config.items()
        if k not in {"runtime_database_user", "runtime_database_password"}
    }
    if (
        digest(original) != envelope["installation_sha256"]
        or config.get("installation_id") != envelope["installation_id"]
    ):
        raise NativeUpgradeError("Installation changed after migration review")
    return config


async def apply_reviewed(
    conn: Any, dsn: str, home: Path, envelope: dict[str, Any], wheel_hash: str
) -> dict[str, Any]:
    """Resume an approved catalog transition without rotating existing secrets."""
    import asyncpg
    from jvspatial.db.indexes import identifier

    from app.services.storage_indexes.catalog import build_catalog
    from app.services.storage_indexes.lifecycle import apply
    from app.services.storage_indexes.provision import serving_dsn
    from app.services.storage_indexes.runtime import inspect

    config = validate(envelope, home, wheel_hash)
    artifact = envelope["plan"]
    role = artifact["runtime_role"]
    specs = build_catalog(include_search=artifact["include_search"])
    # Never adopt an unrelated role, rotate an existing credential, or put its
    # plaintext in the reviewed artifact. The private sidecar makes retries exact.
    secret_path = home / "storage-upgrade-credential.json"
    exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", role)
    if secret_path.exists():
        saved = read_private(secret_path)
        if saved.get("plan_hash") != artifact["hash"] or saved.get("role") != role:
            raise NativeUpgradeError("Another migration credential already exists")
    else:
        if exists:
            raise NativeUpgradeError("Refusing to adopt an existing runtime role")
        saved = {
            "plan_hash": artifact["hash"],
            "role": role,
            "password": secrets.token_hex(32),
        }
        write_private(secret_path, saved)
    password = saved.get("password", "")
    if not re.fullmatch(r"[a-f0-9]{64}", password):
        raise NativeUpgradeError("Invalid private runtime credential")
    for key, value in (
        ("runtime_database_user", role),
        ("runtime_database_password", password),
    ):
        if key in config and config[key] != value:
            raise NativeUpgradeError("Refusing to change existing runtime credentials")
    if not exists:
        await conn.execute(
            f"CREATE ROLE {identifier(role)} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '{password}'"
        )
    await apply(conn, artifact, approved_hash=artifact["hash"], expected_specs=specs)
    runtime = await asyncpg.connect(serving_dsn(dsn, role, password))
    try:
        result = await inspect(runtime, specs, enforce_runtime_role=True)
        if not result["ready"] or result["budget_violations"] or result["invalid"]:
            raise NativeUpgradeError(
                "Migrated storage is not ready with its runtime role"
            )
    finally:
        await runtime.close()
    config.update(runtime_database_user=role, runtime_database_password=password)
    write_private(home / "installation.json", config)
    return {
        "plan_hash": artifact["hash"],
        "runtime_user": role,
        "password_sha256": hashlib.sha256(password.encode()).hexdigest(),
    }


@contextmanager
def private_database(home: Path) -> Iterator[str]:
    """Start the database helper while retaining both native lifecycle locks."""
    from filelock import FileLock

    import app.local_postgres
    from app import local_runtime

    # Offline storage maintenance must not bypass restored-authority quarantine.
    for name in (
        "command.lock",
        "supervisor.lock",
        "recovery.json",
        "postgres",
        "database-runtime",
        "database.json",
        "database.stop.request",
    ):
        if linked(home / name):
            raise NativeUpgradeError("Linked maintenance paths require recovery review")
    if any(linked(path) for path in (home / "postgres").rglob("*")):
        raise NativeUpgradeError(
            "External database paths cannot enter a cold-home migration"
        )
    # Same locks as normal startup, plus a lock held throughout maintenance.
    with (
        FileLock(str(home / "command.lock"), timeout=0),
        FileLock(str(home / "supervisor.lock"), timeout=0),
    ):
        local_runtime._require_recovery_review(home)
        if local_runtime.status(home)["state"] != "stopped":
            raise NativeUpgradeError("Stop the owned runtime before maintenance")
        if (home / "postgres/postmaster.pid").exists() or (
            home / "database.json"
        ).exists():
            raise NativeUpgradeError(
                "Database process or stale readiness requires review"
            )
        if not (home / "postgres/PG_VERSION").is_file():
            raise NativeUpgradeError("Migration requires an existing PostgreSQL home")
        stop = home / "database.stop.request"
        if stop.is_symlink():
            raise NativeUpgradeError("Unsafe database stop file")
        stop.unlink(missing_ok=True)
        child = subprocess.Popen(
            [
                str(local_runtime.database_python(home)),
                app.local_postgres.__file__,
                str(home),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 150
            while not (home / "database.json").exists():
                if child.poll() is not None or time.monotonic() > deadline:
                    raise NativeUpgradeError("Private database did not become ready")
                time.sleep(0.1)
            yield read_private(home / "database.json")["dsn"]
        finally:
            stop.touch(mode=0o600)
            try:
                child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=30)
            if (home / "postgres/postmaster.pid").exists() or (
                home / "database.json"
            ).exists():
                raise NativeUpgradeError(
                    "Maintenance database did not stop; manual recovery required"
                )
            stop.unlink(missing_ok=True)


def configure_parser(parser: argparse.ArgumentParser) -> None:
    """Expose the same offline contract to the public CLI and native clients."""
    parser.add_argument("action", choices=["plan", "apply"])
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approved-hash")
    for name in ("max-indexes", "max-index-bytes", "max-build-bytes"):
        parser.add_argument("--" + name, type=int)


def wheel_identity(wheel: Path) -> str:
    """Bind planning to the maintenance engine actually executing this command.

    A composed desktop wheel may replace UI resources. Its storage engine and
    local process/permission helpers must remain the selected Core artifact's.
    Read named members only; no archive content is extracted or executed.
    """
    if linked(wheel) or not wheel.is_file() or wheel.stat().st_size > 512 * 1024**2:
        raise NativeUpgradeError("Expected a bounded regular candidate wheel")
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "cli.py",
        root / "local_runtime.py",
        root / "local_postgres.py",
        root / "local_permissions.py",
        *sorted(Path(__file__).resolve().parent.glob("*.py")),
        Path(__file__).resolve().with_name("approved.json"),
    ]
    with zipfile.ZipFile(wheel) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise NativeUpgradeError("Candidate wheel has ambiguous archive members")
        for source in files:
            member = "app/" + source.relative_to(root).as_posix()
            if (
                archive.getinfo(member).file_size > 2 * 1024**2
                or archive.read(member) != source.read_bytes()
            ):
                raise NativeUpgradeError(
                    "Candidate wheel does not match the running storage engine"
                )
    with wheel.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def execute(args: argparse.Namespace) -> dict[str, Any]:
    """Run one explicit operator-approved transition; normal boot never calls it."""
    if linked(args.home) or not args.home.is_dir():
        raise NativeUpgradeError("Expected an existing private home")
    home = args.home.resolve()
    if os.name != "nt" and (
        home.stat().st_uid != os.getuid() or home.stat().st_mode & 0o077
    ):
        raise NativeUpgradeError("Native maintenance requires an owner-private home")
    wheel_hash = wheel_identity(args.wheel)
    envelope = None
    if args.action == "apply":
        envelope = read_private(args.plan)
        reviewed = args.plan.read_bytes()
        if (
            not args.approved_hash
            or hashlib.sha256(reviewed).hexdigest() != args.approved_hash
        ):
            raise NativeUpgradeError("Approval must name the exact reviewed file hash")
        # Bind validation/application to the bytes whose whole-file hash was
        # approved, even if an operator replaces the file during this command.
        envelope = json.loads(reviewed)
        if not isinstance(envelope, dict):
            raise NativeUpgradeError("Expected a maintenance object")
        validate(envelope, home, wheel_hash)
    else:
        if args.plan.exists() or linked(args.plan):
            raise NativeUpgradeError("Never overwrite a reviewed migration plan")
        if any(
            getattr(args, name) is None or getattr(args, name) <= 0
            for name in ("max_indexes", "max_index_bytes", "max_build_bytes")
        ):
            raise NativeUpgradeError(
                "Planning requires three explicit positive budgets"
            )
        config_for_plan(home)
    with private_database(home) as dsn:

        async def run() -> dict[str, Any]:
            import asyncpg

            conn = await asyncpg.connect(dsn)
            try:
                if envelope is not None:
                    return await apply_reviewed(conn, dsn, home, envelope, wheel_hash)
                result = await prepare(
                    conn,
                    home,
                    wheel_hash,
                    {
                        name: getattr(args, name)
                        for name in (
                            "max_indexes",
                            "max_index_bytes",
                            "max_build_bytes",
                        )
                    },
                )
                descriptor = os.open(
                    args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "w") as output:
                    json.dump(result, output, indent=2)
                return {
                    "review_required": True,
                    "sha256": hashlib.sha256(args.plan.read_bytes()).hexdigest(),
                }
            finally:
                await conn.close()

        result = asyncio.run(run())
    return {"contract_version": CONTRACT_VERSION, **result}


def run_cli(args: argparse.Namespace) -> int:
    """Do not disclose connection strings through database/subprocess failures."""
    try:
        print(json.dumps(execute(args)))
        return 0
    except NativeUpgradeError as error:
        print(str(error), file=sys.stderr)
    except Exception as error:
        print(
            "Offline storage upgrade failed: "
            + type(error).__name__
            + ". Retain the reviewed plan and use recovery.",
            file=sys.stderr,
        )
    return 1


def main(argv: list[str] | None = None) -> int:
    """Provide an equivalent module entrypoint for diagnostic use."""
    parser = argparse.ArgumentParser(description=__doc__)
    configure_parser(parser)
    return run_cli(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
