"""Explicit disposable backup/restore with independent encrypted state readback."""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive.harness.checkpoint_manifests import persist_checkpoint_manifest
from app.agentive.harness.contracts import (
    HarnessCheckpointManifest,
    HarnessExecutionScope,
)
from app.agentive.harness.jvspatial_store import JvSpatialStepStore
from app.agentive.harness.pydantic_ai_compat import (
    ContinuableSnapshot,
    ModelRequest,
    UserPromptPart,
)
from app.models.credentials import UserModelCredential
from app.services.credential_crypto import encrypt_secret_for_storage
from tests.contract.test_restore_rehearsal import _pg_env
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [
    pytest.mark.contract,
    pytest.mark.postgres,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("INTEGRAL_TEST_RESTORE") != "1",
        reason="Explicit disposable restore gate: set INTEGRAL_TEST_RESTORE=1",
    ),
]
REPO = Path(__file__).resolve().parents[3]

# This runs in a fresh interpreter and a separate restored database. It cannot
# succeed through an inherited GraphContext, entity cache or decrypted payload.
READER = r"""
import asyncio
import json
import os
import sys

from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db import get_database_manager
from jvspatial.db.factory import create_database
from app.agentive.harness.checkpoint_manifests import load_checkpoint_manifest
from app.agentive.harness.contracts import HarnessCheckpointManifest
from app.agentive.harness.jvspatial_store import JvSpatialStepStore, HarnessPersistenceError
from app.models.credentials import UserModelCredential
from app.services.credential_crypto import decrypt_secret_from_storage

async def main():
    data = json.loads(open(sys.argv[1]).read())
    expected = HarnessCheckpointManifest.model_validate(data["manifest"])
    database = create_database(db_type="postgres", dsn=os.environ["JVSPATIAL_POSTGRES_DSN"])
    get_database_manager().set_prime_database(database)
    try:
        async with scoped_default_context_async(GraphContext(database=database)):
            actual = await load_checkpoint_manifest(
                scope=expected.scope, snapshot_id=expected.framework_snapshot_id
            )
            assert actual == expected
            store = JvSpatialStepStore(scope=expected.scope)
            snapshot = await store.latest_snapshot(run_id=expected.scope.run_id)
            assert snapshot is not None
            assert snapshot.messages[0].parts[0].content == "restore checkpoint fixture"
            assert snapshot.idempotency_key == expected.framework_snapshot_id
            credential = await UserModelCredential.get(data["credential_id"])
            assert credential is not None
            assert decrypt_secret_from_storage(
                credential.api_key_enc, aad=credential.id
            ) == "restore credential fixture"
            foreign = expected.scope.model_copy(update={"principal_id": "other-principal"})
            assert await load_checkpoint_manifest(
                scope=foreign, snapshot_id=expected.framework_snapshot_id
            ) is None
            assert await JvSpatialStepStore(scope=foreign).latest_snapshot(
                run_id=expected.scope.run_id
            ) is None
            os.environ["INTEGRAL_CREDENTIAL_ENC_KEY"] = "88" * 32
            os.environ["INTEGRAL_CREDENTIAL_ENC_KEY_PREVIOUS"] = ""
            try:
                await load_checkpoint_manifest(
                    scope=expected.scope, snapshot_id=expected.framework_snapshot_id
                )
            except HarnessPersistenceError:
                pass
            else:
                raise AssertionError("Restored checkpoint accepted the wrong key")
            try:
                await store.latest_snapshot(run_id=expected.scope.run_id)
            except HarnessPersistenceError:
                pass
            else:
                raise AssertionError("Restored transcript accepted the wrong key")
    finally:
        await database.close()
    print("independent-restored-state-readback-ok")

asyncio.run(main())
"""


async def test_backup_restores_encrypted_checkpoint_and_credential(
    postgres_raw_db, monkeypatch, tmp_path
):
    """Restore test-only data, read it from another process, then remove the copy."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "77" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, _ = await proposal()
        identity = uuid4().hex
        scope = HarnessExecutionScope(
            tenant_id=workspace.id,
            workspace_id=workspace.id,
            principal_id=auth.id,
            thread_id=f"restore-thread-{identity}",
            session_id=f"restore-session-{identity}",
            run_id=f"restore-run-{identity}",
            permission_revision="synthetic-restore-permissions",
            capability_version="synthetic-restore-capabilities",
        )
        snapshot_id = f"restore-snapshot-{identity}"
        manifest = HarnessCheckpointManifest(
            scope=scope,
            execution_fence_id=scope.run_id,
            framework_snapshot_id=snapshot_id,
            snapshot_step_index=1,
            snapshot_state="complete",
            safe_for_resume=True,
            framework_codec_version="pydantic-ai-messages-v1",
            capability_fingerprint=scope.capability_version,
            capability_restore_policies={"conversation_messages": "restore"},
            plan_revision="synthetic-restore-plan",
        )
        await persist_checkpoint_manifest(manifest)
        await JvSpatialStepStore(scope=scope).save_snapshot(
            ContinuableSnapshot(
                run_id=scope.run_id,
                conversation_id=scope.session_id,
                step_index=1,
                timestamp=datetime.now(timezone.utc),
                messages=[
                    ModelRequest(parts=[UserPromptPart("restore checkpoint fixture")])
                ],
                idempotency_key=snapshot_id,
            )
        )
        credential_id = f"o.UserModelCredential.restore-{identity}"
        await UserModelCredential.create(
            id=credential_id,
            user_id=auth.id,
            provider="synthetic-restore-provider",
            api_key_enc=encrypt_secret_for_storage(
                "restore credential fixture", aad=credential_id
            ),
        )
        rows = await postgres_raw_db.find("object", {})
        assert "restore checkpoint fixture" not in str(rows)
        assert "restore credential fixture" not in str(rows)

        env = _pg_env()
        env["POSTGRES_HOST"] = os.getenv(
            "INTEGRAL_RESTORE_CLIENT_HOST", env["POSTGRES_HOST"]
        )
        env["BACKUP_DIR"] = str(tmp_path)
        backup = subprocess.run(
            ["bash", str(REPO / "scripts/pg_backup.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert backup.returncode == 0, backup.stderr or backup.stdout
        dumps = list(tmp_path.glob("integral-*.dump"))
        assert len(dumps) == 1
        source_dsn = os.environ["JVSPATIAL_POSTGRES_DSN"]
        parts = urlparse(source_dsn)
        admin = await asyncpg.connect(urlunparse(parts._replace(path="/postgres")))
        target = f"integral_restore_{identity}"
        try:
            await admin.execute(f'CREATE DATABASE "{target}"')
            restored = subprocess.run(
                [
                    "bash",
                    str(REPO / "scripts/pg_restore.sh"),
                    str(dumps[0]),
                    "--into",
                    target,
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert restored.returncode == 0, restored.stderr or restored.stdout
            expected = tmp_path / "expected.json"
            expected.write_text(
                json.dumps(
                    {
                        "manifest": manifest.model_dump(mode="json"),
                        "credential_id": credential_id,
                    }
                )
            )
            reader_env = env.copy()
            reader_env["JVSPATIAL_POSTGRES_DSN"] = urlunparse(
                parts._replace(path=f"/{target}")
            )
            reader = subprocess.run(
                [sys.executable, "-c", READER, str(expected)],
                env=reader_env,
                cwd=str(REPO / "backend"),
                capture_output=True,
                text=True,
                timeout=90,
            )
            assert reader.returncode == 0, reader.stderr or reader.stdout
            assert "independent-restored-state-readback-ok" in reader.stdout
            drill = subprocess.run(
                ["bash", str(REPO / "scripts/pg_restore.sh"), str(dumps[0]), "--drill"],
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert drill.returncode == 0, drill.stderr or drill.stdout
            assert "drill: counts match" in drill.stdout
            assert "drill: identity match" in drill.stdout
            assert "dropping scratch database" in drill.stdout
            scratch = re.findall(
                r"drill: creating scratch database (integral_drill_[0-9]{14})",
                drill.stdout,
            )
            assert len(scratch) == 1
            assert (
                await admin.fetchval(
                    "SELECT 1 FROM pg_database WHERE datname = $1", scratch[0]
                )
                is None
            )
        finally:
            try:
                await admin.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')
                assert (
                    await admin.fetchval(
                        "SELECT 1 FROM pg_database WHERE datname = $1", target
                    )
                    is None
                )
            finally:
                await admin.close()
