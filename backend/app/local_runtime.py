"""Public local installation lifecycle used by the CLI and native clients.

The supervisor owns the PostgreSQL helper, API, and workspace processes. Data
and keys live outside the disposable Python tool environment. No domain App or
client implementation is imported by this module.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import psutil
from filelock import FileLock, Timeout

CONTRACT_VERSION = 1
PGSERVER_VERSION = "0.1.4"


class RuntimeErrorDetail(RuntimeError):
    """An actionable local-runtime failure safe to display to the user."""


def default_home() -> Path:
    """Return persistent, platform-appropriate data storage."""
    if os.environ.get("INTEGRAL_HOME"):
        return Path(os.environ["INTEGRAL_HOME"]).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Integral/Core"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Integral/Core"
    return (
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "integral"
    )


def private_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically retain private configuration without a partially written file."""
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, indent=2)
    os.replace(temporary, path)
    path.chmod(0o600)


def read_json(path: Path) -> dict[str, Any]:
    """Read a control file, treating a missing file as an absent installation."""
    return json.loads(path.read_text()) if path.exists() else {}


def initialize(home: Path) -> dict[str, Any]:
    """Create once; reruns preserve keys, identity, and user data."""
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    path = home / "installation.json"
    with FileLock(str(home / "initialize.lock")):
        try:
            config = read_json(path)
        except (ValueError, OSError) as exc:
            raise RuntimeErrorDetail(
                "Installation configuration could not be read. Restore installation.json "
                "from this installation's backup; existing keys were not changed."
            ) from exc
        if not path.exists():
            config = {
                "contract_version": CONTRACT_VERSION,
                "installation_id": secrets.token_hex(16),
                "jwt_key": secrets.token_hex(32),
                "credential_key": base64.b64encode(secrets.token_bytes(32)).decode(),
                "oauth_key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
                "database_password": secrets.token_hex(32),
                "control_token": secrets.token_hex(32),
            }
            private_json(path, config)
    if (
        not isinstance(config, dict)
        or config.get("contract_version") != CONTRACT_VERSION
    ):
        raise RuntimeErrorDetail(
            "Unsupported installation format; use its matching Core release or "
            "restore installation.json from this installation's backup."
        )
    # Never repair installation secrets by generating replacements: persisted
    # harness state, provider keys and login sessions depend on this identity.
    from cryptography.fernet import Fernet

    for key in (
        "jwt_key",
        "credential_key",
        "oauth_key",
        "database_password",
        "control_token",
        "installation_id",
    ):
        value = config.get(key)
        valid = isinstance(value, str) and bool(value.strip())
        if valid and key in {"jwt_key", "database_password", "control_token"}:
            valid = len(value) >= 32
        if valid and key == "credential_key":
            try:
                valid = (
                    len(base64.b64decode(value, altchars=b"-_", validate=True)) == 32
                )
            except ValueError:
                valid = False
        if valid and key == "oauth_key":
            try:
                Fernet(value.encode("ascii"))
            except ValueError:
                valid = False
        if not valid:
            raise RuntimeErrorDetail(
                f"Installation {key} is missing or invalid. Existing keys were not changed. "
                "Restore installation.json from this installation's backup before starting."
            )
    for directory in ("files", "logs", "integral-apps"):
        (home / directory).mkdir(exist_ok=True)
    return config


def _process(state: dict[str, Any]) -> psutil.Process | None:
    try:
        process = psutil.Process(int(state["pid"]))
        if abs(process.create_time() - float(state["created"])) < 0.01:
            return (
                process
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE
                else None
            )
    except (KeyError, ValueError, psutil.Error):
        pass
    return None


def _identity(home: Path, state: dict[str, Any]) -> bool:
    config = read_json(home / "installation.json")
    try:
        request = urllib.request.Request(
            state["web_url"] + "/_integral/runtime",
            headers={"Authorization": "Bearer " + config["control_token"]},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.load(response)
        return payload.get("installation_id") == config["installation_id"]
    except (KeyError, OSError, ValueError):
        return False


def status(home: Path) -> dict[str, Any]:
    """Return a secret-free lifecycle descriptor for people and native clients."""
    state = read_json(home / "runtime.json")
    alive = _process(state) is not None
    ready = alive and state.get("state") == "ready" and _identity(home, state)
    return {
        "contract_version": CONTRACT_VERSION,
        "default_harness_provider_id": "integral_native",
        "installation_id": read_json(home / "installation.json").get("installation_id"),
        "state": "ready" if ready else ("starting" if alive else "stopped"),
        "home": str(home),
        "api_url": state.get("api_url"),
        "web_url": state.get("web_url"),
        "version": state.get("version"),
        "harness_provider_id": state.get("harness_provider_id"),
        "pid": state.get("pid") if alive else None,
        "process_created": state.get("created") if alive else None,
        "database": "managed-postgresql",
    }


def _uv() -> list[str]:
    binary = shutil.which("uv")
    if binary:
        return [binary]
    try:
        import uv

        return [str(uv.find_uv_bin())]
    except ImportError as exc:
        raise RuntimeErrorDetail(
            "Install uv to provision the local runtime: https://docs.astral.sh/uv/"
        ) from exc


def database_python(home: Path) -> Path:
    """Resolve pgserver's supported interpreter in its own pinned environment."""
    env = home / "database-runtime"
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    stamp = env / "integral-pgserver-version"
    if python.is_file() and stamp.exists() and stamp.read_text() == PGSERVER_VERSION:
        return python
    subprocess.run(
        [*_uv(), "venv", "--python", "3.12", str(env)], check=True, stdout=sys.stderr
    )
    subprocess.run(
        [
            *_uv(),
            "pip",
            "install",
            "--python",
            str(python),
            "--index-url",
            "https://pypi.org/simple",
            "--only-binary=:all:",
            f"pgserver=={PGSERVER_VERSION}",
        ],
        check=True,
        stdout=sys.stderr,
    )
    stamp.write_text(PGSERVER_VERSION)
    return python


def free_port(preferred: int) -> int:
    """Keep the familiar port when available; otherwise choose an unused one."""
    # A wildcard IPv6 listener (including Docker port forwarding on macOS)
    # can accept IPv4 traffic while a loopback bind still succeeds. Probe the
    # endpoint first so startup cannot attach clients to another installation.
    if preferred:
        try:
            with socket.create_connection(("127.0.0.1", preferred), timeout=0.2):
                preferred = 0
        except OSError:
            pass
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
        except OSError:
            sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(
    home: Path,
    *,
    port: int | None = None,
    open_browser: bool = True,
    apps: Path | None = None,
    timeout: float = 180,
) -> dict[str, Any]:
    """Start once and wait for actual readiness, or attach to the same instance."""
    initialize(home)
    _operator_settings(home)
    lock = FileLock(str(home / "command.lock"))
    try:
        with lock.acquire(timeout=timeout):
            current = status(home)
            if (
                current["state"] != "stopped"
                and current.get("harness_provider_id") != "integral_native"
            ):
                raise RuntimeErrorDetail(
                    "This installation is running a pre-native Core runtime. "
                    "Run integral stop, then integral up with the native Core release."
                )
            launched = current["state"] == "stopped"
            config = read_json(home / "installation.json")
            desired_apps = str(apps.resolve()) if apps else config.get("apps_path")
            prior = read_json(home / "runtime.json")
            if current["state"] != "stopped" and prior.get("apps") != desired_apps:
                raise RuntimeErrorDetail(
                    "This installation is running with different App paths. Stop it before changing --apps."
                )
            if current["state"] == "stopped":
                from app.web.server import web_static_dir

                if web_static_dir() is None:
                    raise RuntimeErrorDetail(
                        "The installed Core has no packaged UI. Build with .ci/bundle_web_assets.sh or install the released wheel."
                    )
                if os.name != "nt" and os.getuid() == 0:
                    raise RuntimeErrorDetail(
                        "Run Integral as your normal user, not root."
                    )
                database_python(home)
                if not config.get("api_port") and prior.get("api_url"):
                    config["api_port"] = int(prior["api_url"].rsplit(":", 1)[1])
                preferred_web = (
                    port if port is not None else config.get("web_port", 9006)
                )
                if port is None and not config.get("web_port") and prior.get("web_url"):
                    preferred_web = int(prior["web_url"].rsplit(":", 1)[1])
                if desired_apps:
                    if not Path(desired_apps).is_dir():
                        raise RuntimeErrorDetail(
                            "The configured App directory is missing. Supply --apps with its restored location."
                        )
                    config["apps_path"] = desired_apps
                private_json(home / "installation.json", config)
                selected = config.get("runtime_python", sys.executable)
                if not Path(selected).is_file():
                    raise RuntimeErrorDetail(
                        "The selected runtime is missing. Restore its environment or select a release with integral upgrade."
                    )
                private_json(home / "runtime.json", {})
                args = [
                    selected,
                    "-m",
                    "app.local_runtime",
                    "supervise",
                    str(home),
                    str(preferred_web),
                ]
                if desired_apps:
                    args.append(desired_apps)
                env = dict(os.environ)
                env.pop("PYTHONPATH", None)
                if selected == sys.executable:
                    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
                with (home / "logs/supervisor.log").open("ab") as log:
                    subprocess.Popen(
                        args,
                        cwd=home,
                        env=env,
                        stdout=log,
                        stderr=log,
                        start_new_session=os.name != "nt",
                        creationflags=(
                            subprocess.CREATE_NEW_PROCESS_GROUP
                            if os.name == "nt"
                            else 0
                        ),
                    )
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                current = status(home)
                if current["state"] == "ready":
                    if open_browser:
                        webbrowser.open(current["web_url"])
                    return {**current, "started": launched}
                error = read_json(home / "runtime.json").get("error")
                if error:
                    raise RuntimeErrorDetail(
                        f"Startup failed: {error}. See integral logs --home {home}"
                    )
                time.sleep(0.3)
            _stop(home)
            raise RuntimeErrorDetail(
                f"Startup timed out. See integral logs --home {home}"
            )
    except Timeout as exc:
        raise RuntimeErrorDetail(
            "Another Integral command is still using this installation."
        ) from exc


def _stop(home: Path) -> dict[str, Any]:
    """Stop only the supervisor with the recorded process birth time."""
    state = read_json(home / "runtime.json")
    process = _process(state)
    if process:
        if os.name == "nt":
            (home / "stop.request").touch()
        else:
            process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=45)
        except psutil.TimeoutExpired as exc:
            raise RuntimeErrorDetail(
                "Shutdown is still in progress; inspect logs before retrying."
            ) from exc
    return status(home)


def stop(
    home: Path,
    *,
    expected_pid: int | None = None,
    expected_created: float | None = None,
) -> dict[str, Any]:
    """Serialize shutdown with startup and backups."""
    if not home.exists():
        return status(home)
    with FileLock(str(home / "command.lock"), timeout=240):
        state = read_json(home / "runtime.json")
        if expected_pid is not None and (
            state.get("pid") != expected_pid or state.get("created") != expected_created
        ):
            return status(home)
        return _stop(home)


def _system_environment() -> dict[str, str]:
    """Retain OS and network configuration, never another app's settings."""
    names = {
        "PATH",
        "HOME",
        "USER",
        "USERNAME",
        "LOGNAME",
        "USERPROFILE",
        "TMP",
        "TEMP",
        "TMPDIR",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
        "LANG",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
        "__CF_USER_TEXT_ENCODING",
    }
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in names or key.startswith(("LC_", "XDG_"))
    }


def _operator_settings(home: Path) -> dict[str, str]:
    """Read one explicit settings file, rejecting ambiguous dotenv syntax."""
    from dotenv.parser import parse_stream
    from dotenv.variables import Variable, parse_variables

    path = home / "settings.env"
    if not path.exists():
        return {}
    seen: set[str] = set()
    values: dict[str, str] = {}
    context = _system_environment()
    try:
        with path.open(encoding="utf-8") as handle:
            for binding in parse_stream(handle):
                if binding.error or (binding.key and binding.value is None):
                    raise RuntimeErrorDetail(
                        f"Invalid settings.env assignment at line {binding.original.line}. "
                        "Use NAME=value, then restart this installation."
                    )
                if binding.key:
                    if binding.key in (
                        "TESTING",
                        "test_mode",
                    ) or binding.key.startswith("PYTEST_"):
                        raise RuntimeErrorDetail(
                            f"Reserved test setting in settings.env at line {binding.original.line}. "
                            "Remove it; managed installations always use real authentication."
                        )
                    if binding.key in seen:
                        raise RuntimeErrorDetail(
                            f"Duplicate settings.env assignment at line {binding.original.line}. "
                            "Keep one assignment per setting, then restart this installation."
                        )
                    seen.add(binding.key)
                    atoms = list(parse_variables(binding.value))
                    for atom in atoms:
                        if (
                            isinstance(atom, Variable)
                            and atom.name not in context
                            and atom.default is None
                        ):
                            raise RuntimeErrorDetail(
                                f"Unresolved settings.env reference at line {binding.original.line}. "
                                "Define referenced settings earlier in this file, "
                                "supply a ${NAME:-default}, or use a literal value."
                            )
                    value = "".join(atom.resolve(context) for atom in atoms)
                    values[binding.key] = value
                    context[binding.key] = value
        return values
    except (OSError, UnicodeError) as exc:
        raise RuntimeErrorDetail(
            "The installation's settings.env could not be read. Check its encoding "
            "and file permissions, then restart. Existing keys were not changed."
        ) from exc


def _environment(
    home: Path,
    config: dict[str, Any],
    dsn: str,
    api_port: int,
    web_port: int,
    apps: str | None,
) -> dict[str, str]:
    """Isolate managed settings from checkout/distro dotenv files and test flags."""
    env = _system_environment()
    env.update(_operator_settings(home))
    controlled = {
        "DEBUG": "false",
        "WORKERS": "1",
        "JVSPATIAL_DEBUG": "false",
        "JVSPATIAL_AUTH_ENABLED": "true",
        "JVSPATIAL_HOST": "127.0.0.1",
        "JVSPATIAL_PORT": str(api_port),
        "JVSPATIAL_DB_TYPE": "postgres",
        "JVSPATIAL_POSTGRES_DSN": dsn,
        "JVSPATIAL_LOG_DB_TYPE": "postgres",
        "JVSPATIAL_LOG_POSTGRES_DSN": dsn,
        "JVSPATIAL_JWT_SECRET_KEY": config["jwt_key"],
        "INTEGRAL_CREDENTIAL_ENC_KEY": config["credential_key"],
        "JVSPATIAL_OAUTH_KEY_ENCRYPTION_KEY": config["oauth_key"],
        "INTEGRAL_CORE_ONLY": "0" if apps else "1",
        "INTEGRAL_PACKAGE_PATHS": apps or str(home / "integral-apps"),
        "INTEGRAL_ENV_FILE": str(home / "settings.env"),
        "JVSPATIAL_FILES_ROOT_PATH": str(home / "files"),
        "JVSPATIAL_CORS_ORIGINS": f"http://127.0.0.1:{web_port}",
        "OAUTH_ISSUER_URL": f"http://127.0.0.1:{api_port}",
        "FRONTEND_ORIGIN": f"http://127.0.0.1:{web_port}",
        "APP_BASE_URL": f"http://127.0.0.1:{web_port}",
        "EMAIL_PROVIDER": "console",
        "RATE_LIMIT_DISABLED": "0",
        "INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED": "false",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    # Platform keys are opt-in; startup never inherits a checkout's paid keys.
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "SERPER_API_KEY",
        "ADMIN_EMAIL",
        "ADMIN_PASSWORD",
    ):
        env.setdefault(key, "")
    env.update(controlled)
    return env


def supervise(home: Path, port: int, apps: str | None = None) -> None:
    """Own children, readiness and cleanup independently of the calling terminal."""
    with FileLock(str(home / "supervisor.lock"), timeout=0):
        config = initialize(home)
        (home / "stop.request").unlink(missing_ok=True)
        (home / "database.json").unlink(missing_ok=True)
        (home / "database.stop.request").unlink(missing_ok=True)
        process = psutil.Process()
        api_port, web_port = free_port(config.get("api_port", 4000)), free_port(port)
        if api_port == web_port:
            web_port = free_port(0)
        config.update({"api_port": api_port, "web_port": web_port})
        private_json(home / "installation.json", config)
        try:
            core_version = version("integral-core")
        except PackageNotFoundError:
            core_version = "source"
        state = {
            "pid": process.pid,
            "created": process.create_time(),
            "state": "starting",
            "api_url": f"http://127.0.0.1:{api_port}",
            "web_url": f"http://127.0.0.1:{web_port}",
            "version": core_version,
            "harness_provider_id": "integral_native",
            "apps": apps,
        }
        private_json(home / "runtime.json", state)
        children = []
        stopping = False

        def request_stop(_signum, _frame):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)

        def child(args, name, env=None):
            with (home / "logs" / f"{name}.log").open("ab") as log:
                spawned = subprocess.Popen(
                    args, cwd=home, env=env, stdout=log, stderr=log
                )
            children.append(spawned)
            return spawned

        try:
            child(
                [
                    str(database_python(home)),
                    str(Path(__file__).with_name("local_postgres.py")),
                    str(home),
                ],
                "database",
            )
            deadline = time.monotonic() + 150
            while not (home / "database.json").exists():
                if stopping or (home / "stop.request").exists():
                    return
                if children[0].poll() is not None or time.monotonic() > deadline:
                    raise RuntimeErrorDetail("Private PostgreSQL failed to start")
                time.sleep(0.2)
            dsn = read_json(home / "database.json")["dsn"]
            env = _environment(home, config, dsn, api_port, web_port, apps)
            child([sys.executable, "-m", "app.main"], "api", env)
            child(
                [sys.executable, "-m", "app.local_runtime", "web", str(home)],
                "web",
                env,
            )
            while not stopping and not (home / "stop.request").exists():
                if any(item.poll() is not None for item in children):
                    raise RuntimeErrorDetail(
                        "A runtime process exited; inspect its log"
                    )
                if state["state"] != "ready":
                    try:
                        with urllib.request.urlopen(
                            state["api_url"] + "/health", timeout=1
                        ) as response:
                            healthy = response.status == 200
                        if healthy and _identity(home, state):
                            state["state"] = "ready"
                            private_json(home / "runtime.json", state)
                    except OSError:
                        pass
                    if time.monotonic() > deadline:
                        raise RuntimeErrorDetail("API readiness timed out")
                time.sleep(0.3)
        except Exception as exc:
            state["error"] = (
                str(exc)
                if isinstance(exc, RuntimeErrorDetail)
                else f"{type(exc).__name__}; inspect supervisor.log"
            )
        finally:
            for item in reversed(children):
                if item.poll() is None:
                    if item is children[0]:
                        (home / "database.stop.request").touch()
                    else:
                        item.terminate()
                    try:
                        item.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        item.kill()
                        item.wait()
            state["state"] = "stopped"
            private_json(home / "runtime.json", state)
            (home / "stop.request").unlink(missing_ok=True)


def serve_workspace(home: Path) -> None:
    """Serve the packaged UI with a private installation identity handshake."""
    import uvicorn
    from starlette.responses import JSONResponse

    from app.web.server import create_web_app, web_static_dir

    config = read_json(home / "installation.json")
    state = read_json(home / "runtime.json")
    workspace = create_web_app(web_static_dir(), state["api_url"])

    async def control_app(scope, receive, send):
        if scope.get("path") == "/_integral/runtime":
            headers = dict(scope.get("headers", []))
            supplied = headers.get(b"authorization", b"").decode()
            authorized = secrets.compare_digest(
                supplied, "Bearer " + config["control_token"]
            )
            response = JSONResponse(
                (
                    {
                        "installation_id": config["installation_id"],
                        "contract_version": CONTRACT_VERSION,
                    }
                    if authorized
                    else {"error": "unauthorized"}
                ),
                status_code=200 if authorized else 401,
            )
            await response(scope, receive, send)
        else:
            await workspace(scope, receive, send)

    uvicorn.run(
        control_app,
        host="127.0.0.1",
        port=int(state["web_url"].rsplit(":", 1)[1]),
        log_level="warning",
    )


def backup(home: Path, destination: Path) -> Path:
    """Create a consistent cold backup; leave the installation stopped."""
    if destination.resolve().is_relative_to(home.resolve()):
        raise RuntimeErrorDetail("Save backups outside the installation directory.")
    if not home.exists():
        raise RuntimeErrorDetail("No installation exists to back up.")
    with FileLock(str(home / "command.lock"), timeout=240):
        return _backup(home, destination)


def _backup(home: Path, destination: Path) -> Path:
    _stop(home)
    if not (home / "installation.json").exists():
        raise RuntimeErrorDetail("No installation exists to back up.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    config = read_json(home / "installation.json")
    try:
        with (
            os.fdopen(fd, "wb") as stream,
            tarfile.open(fileobj=stream, mode="w:gz") as archive,
        ):
            for name in (
                "installation.json",
                "settings.env",
                "postgres",
                "files",
                "integral-apps",
            ):
                source = (
                    Path(config["apps_path"])
                    if name == "integral-apps" and config.get("apps_path")
                    else home / name
                )
                if source.exists():
                    links = (
                        any(path.is_symlink() for path in source.rglob("*"))
                        if source.is_dir()
                        else source.is_symlink()
                    )
                    if links or source.is_symlink():
                        raise RuntimeErrorDetail(
                            "Backup refuses symbolic links; retain linked files separately."
                        )
                    archive.add(source, arcname=name)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def restore(home: Path, source: Path) -> None:
    """Restore only into an empty installation, refusing traversal and links."""
    if home.exists() and any(home.iterdir()):
        raise RuntimeErrorDetail("Restore requires an empty --home directory.")
    home.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="integral-restore-", dir=home.parent
    ) as staging:
        staged = Path(staging) / "installation"
        staged.mkdir(mode=0o700)
        with tarfile.open(source, "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                path = Path(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in member.name
                    or not (member.isfile() or member.isdir())
                ):
                    raise RuntimeErrorDetail("Backup contains an unsafe path or link.")
            archive.extractall(staged, members=members)
        if (staged / "postgres").is_dir():
            (staged / "postgres").chmod(0o700)
        if not read_json(staged / "installation.json"):
            raise RuntimeErrorDetail("Backup contains no installation identity.")
        initialize(staged)
        config = read_json(staged / "installation.json")
        config.pop("runtime_python", None)
        if config.get("apps_path"):
            config["apps_path"] = str(home / "integral-apps")
        private_json(staged / "installation.json", config)
        if home.exists():
            home.rmdir()
        staged.rename(home)


def upgrade(
    home: Path,
    *,
    release: str | None = None,
    wheel: Path | None = None,
    dependency_wheels: list[Path] | None = None,
) -> dict[str, Any]:
    """Install a chosen candidate separately, back up, and start its launcher."""
    if not release and not wheel:
        raise RuntimeErrorDetail(
            "Choose --version or --wheel; upgrades never silently follow latest."
        )
    if release and not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", release):
        raise RuntimeErrorDetail("Use a concrete release version.")
    stamp = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(4)
    )
    candidate = home.parent / f"{home.name}-releases" / stamp
    subprocess.run(
        [*_uv(), "venv", "--python", "3.12", str(candidate)],
        check=True,
        stdout=sys.stderr,
    )
    python = candidate / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    requirement = str(wheel.resolve()) if wheel else f"integral-core=={release}"
    subprocess.run(
        [
            *_uv(),
            "pip",
            "install",
            "--python",
            str(python),
            "--index-url",
            "https://pypi.org/simple",
            requirement,
            *[str(path.resolve()) for path in dependency_wheels or []],
        ],
        check=True,
        stdout=sys.stderr,
    )
    # Prove the launcher exists before stopping or backing up this instance.
    subprocess.run(
        [str(python), "-m", "app.cli", "status", "--home", str(home), "--json"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    destination = (
        home.parent / f"{home.name}-backups" / f"before-upgrade-{stamp}.tar.gz"
    )
    with FileLock(str(home / "command.lock"), timeout=240):
        prior = read_json(home / "runtime.json")
        _backup(home, destination)
        config = read_json(home / "installation.json")
        config["runtime_python"] = str(python)
        private_json(home / "installation.json", config)
    command = [
        str(python),
        "-m",
        "app.cli",
        "up",
        "--home",
        str(home),
        "--no-open",
        "--json",
    ]
    if prior.get("apps"):
        command.extend(["--apps", prior["apps"]])
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeErrorDetail(
            f"Upgrade could not start. Your pre-upgrade backup is {destination}; restore it into an empty directory with the previous Core release."
        )
    return {**json.loads(result.stdout), "backup": str(destination)}


if __name__ == "__main__":
    if sys.argv[1] == "supervise":
        supervise(
            Path(sys.argv[2]),
            int(sys.argv[3]),
            sys.argv[4] if len(sys.argv) > 4 else None,
        )
    elif sys.argv[1] == "web":
        serve_workspace(Path(sys.argv[2]))
