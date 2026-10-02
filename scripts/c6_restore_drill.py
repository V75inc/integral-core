"""Populate the live main API, restore Postgres and /data, and re-download.

Qualification script for C6 A14. Not imported by the application. Calls the
public signup, app, track, entry, and attachment endpoints, then restores a
scratch database and file volume from the running integral-main-smoke stack.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

API = os.environ.get("C6_API", "http://127.0.0.1:14000")
EVIDENCE = Path(os.environ.get("EVIDENCE_DIR", "/tmp/c6-a14-evidence"))
SOURCE_API = "integral-main-smoke-api-1"
PG = "integral-pg"
NETWORK = "integral-main-smoke_default"
SOURCE_VOLUME = "integral-main-smoke_integral_db"
SCRATCH_DB = "c6_a14_restore"
SCRATCH_VOLUME = "c6-a14-files"
SCRATCH_API = "c6-a14-api"
SCRATCH_PORT = "14021"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    return subprocess.run(cmd, check=True, text=True, **kwargs)


def request(
    method: str, url: str, token: str | None = None, body=None, data=None, headers=None
):
    hdrs = dict(headers or {})
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
            content_type = response.headers.get("content-type", "")
            parsed = (
                json.loads(raw.decode() or "null")
                if "application/json" in content_type
                else raw
            )
            return response.status, parsed
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise SystemExit(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    stamp = uuid.uuid4().hex[:8]
    payload = f"c6-a14-{stamp}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    _, signup = request(
        "POST",
        f"{API}/api/auth/signup",
        body={
            "email": f"c6-a14-{stamp}@example.com",
            "password": f"C6-a14-{stamp}-aZ7!",
            "name": "C6 A14",
            "workspaceName": f"C6 A14 {stamp}",
        },
    )
    token = signup["access_token"]
    workspace_id = signup["workspace"]["id"]
    scope = {"X-Integral-Scope": f"ws:{workspace_id}"}
    _, app = request(
        "POST",
        f"{API}/api/apps",
        token,
        {"name": f"C6 A14 App {stamp}", "workspace_id": workspace_id},
        headers=scope,
    )
    _, track = request(
        "POST",
        f"{API}/api/tracks",
        token,
        {
            "title": f"C6 A14 Track {stamp}",
            "app_id": app["app"]["id"],
            "workspace_id": workspace_id,
        },
        headers=scope,
    )
    _, entry = request(
        "POST",
        f"{API}/api/entries",
        token,
        {
            "track_id": track["track"]["id"],
            "title": f"C6 A14 Entry {stamp}",
            "body": "restore drill",
        },
        headers=scope,
    )
    entry_id = entry["entry"]["id"]
    upload = subprocess.run(
        [
            "curl",
            "-fsS",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            f"X-Integral-Scope: ws:{workspace_id}",
            "-F",
            f"file=@-;filename=c6-a14-{stamp}.txt;type=text/plain",
            f"{API}/api/entries/{entry_id}/attachments",
        ],
        input=payload,
        capture_output=True,
        check=False,
    )
    if upload.returncode != 0:
        raise SystemExit(upload.stderr.decode()[:500] or "upload failed")
    uploaded = json.loads(upload.stdout.decode())
    attachment_id = uploaded["attachment"]["id"]
    _, original = request(
        "GET",
        f"{API}/api/attachments/{attachment_id}/download",
        token,
        headers=scope,
    )
    if hashlib.sha256(original).hexdigest() != digest:
        raise SystemExit("original download hash mismatch")

    dump = EVIDENCE / "c6-a14.dump"
    run(
        [
            "docker",
            "exec",
            PG,
            "pg_dump",
            "-U",
            "integral",
            "-d",
            "integral",
            "-Fc",
            "-f",
            "/tmp/c6-a14.dump",
        ]
    )
    run(["docker", "cp", f"{PG}:/tmp/c6-a14.dump", str(dump)])
    run(
        [
            "docker",
            "exec",
            PG,
            "psql",
            "-U",
            "integral",
            "-d",
            "postgres",
            "-c",
            f"DROP DATABASE IF EXISTS {SCRATCH_DB}",
        ]
    )
    run(
        [
            "docker",
            "exec",
            PG,
            "psql",
            "-U",
            "integral",
            "-d",
            "postgres",
            "-c",
            f"CREATE DATABASE {SCRATCH_DB}",
        ]
    )
    run(["docker", "cp", str(dump), f"{PG}:/tmp/c6-a14.dump"])
    run(
        [
            "docker",
            "exec",
            PG,
            "pg_restore",
            "-U",
            "integral",
            "-d",
            SCRATCH_DB,
            "--no-owner",
            "--exit-on-error",
            "/tmp/c6-a14.dump",
        ]
    )
    subprocess.run(
        ["docker", "volume", "rm", "-f", SCRATCH_VOLUME],
        check=False,
        capture_output=True,
    )
    run(["docker", "volume", "create", SCRATCH_VOLUME])
    run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{SOURCE_VOLUME}:/from:ro",
            "-v",
            f"{SCRATCH_VOLUME}:/to",
            "alpine:3.20",
            "sh",
            "-c",
            "cd /from && tar -cf - . | tar -C /to -xf -",
        ]
    )
    env_text = run(
        [
            "docker",
            "inspect",
            SOURCE_API,
            "--format",
            "{{range .Config.Env}}{{println .}}{{end}}",
        ]
    ).stdout
    env_file = EVIDENCE / "scratch.env"
    lines = []
    for line in env_text.splitlines():
        if line.startswith("JVSPATIAL_POSTGRES_DSN="):
            lines.append(
                f"JVSPATIAL_POSTGRES_DSN=postgresql://integral:integral@db:5432/{SCRATCH_DB}"
            )
        elif line.startswith("POSTGRES_DB="):
            lines.append(f"POSTGRES_DB={SCRATCH_DB}")
        else:
            lines.append(line)
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(
        ["docker", "rm", "-f", SCRATCH_API], check=False, capture_output=True
    )
    image = run(
        ["docker", "inspect", SOURCE_API, "--format", "{{.Config.Image}}"]
    ).stdout.strip()
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            SCRATCH_API,
            "--network",
            NETWORK,
            "--env-file",
            str(env_file),
            "-p",
            f"127.0.0.1:{SCRATCH_PORT}:4000",
            "-v",
            f"{SCRATCH_VOLUME}:/data",
            image,
        ]
    )
    restored = b""
    deadline = time.time() + 90
    last_error = "scratch API did not serve the attachment"
    while time.time() < deadline:
        try:
            _, restored = request(
                "GET",
                f"http://127.0.0.1:{SCRATCH_PORT}/api/attachments/{attachment_id}/download",
                token,
                headers=scope,
            )
            break
        except (
            SystemExit,
            Exception,
        ) as exc:  # noqa: BLE001 — scratch API may still be booting
            last_error = str(exc)
            time.sleep(2)
    else:
        logs = subprocess.run(
            ["docker", "logs", "--tail", "40", SCRATCH_API],
            capture_output=True,
            text=True,
        )
        raise SystemExit(f"{last_error}\n{logs.stderr[-800:]}")
    restored_digest = hashlib.sha256(restored).hexdigest()
    if restored_digest != digest:
        raise SystemExit("restored download hash mismatch")
    result = {
        "result": "passed",
        "attachment_id": attachment_id,
        "entry_id": entry_id,
        "sha256": digest,
        "restored_sha256": restored_digest,
        "bytes": len(payload),
        "source_image": image,
        "scratch_database": SCRATCH_DB,
    }
    (EVIDENCE / "restore.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    finally:
        subprocess.run(
            ["docker", "rm", "-f", SCRATCH_API], check=False, capture_output=True
        )
        subprocess.run(
            [
                "docker",
                "exec",
                PG,
                "psql",
                "-U",
                "integral",
                "-d",
                "postgres",
                "-c",
                f"DROP DATABASE IF EXISTS {SCRATCH_DB}",
            ],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["docker", "volume", "rm", "-f", SCRATCH_VOLUME],
            check=False,
            capture_output=True,
        )
