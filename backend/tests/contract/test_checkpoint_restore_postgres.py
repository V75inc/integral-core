"""Explicit disposable backup/restore with independent encrypted state readback."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import artifacts
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
from app.agentive.services.work_execution import build_work_execution_context
from app.agentive.services.work_items import claim_due_candidate
from app.models.credentials import UserModelCredential
from app.services import attachment_storage
from app.services.chat_attachment_review import inspect_chat_attachment_for_review
from app.services.chat_threads import create_thread
from app.services.credential_crypto import encrypt_secret_for_storage
from app.services.document_draft_access import load_document_draft_snapshot
from app.services.document_draft_preparation import prepare_document_draft
from tests.contract.test_restore_rehearsal import _pg_env
from tests.contract.test_staging_command_apply_postgres import proposal
from tests.contract.test_work_budgets_postgres import approved_root, request
from tests.contract.test_work_model_receipts_postgres import marked_case
from tests.test_work_mandate_contract import mandate_payload

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
import hashlib
import json
import os
import sys

from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db import get_database_manager
from jvspatial.db.factory import create_database
from app.agentive.harness.checkpoint_manifests import load_checkpoint_manifest
from app.agentive.harness.contracts import HarnessCheckpointManifest
from app.agentive.harness.jvspatial_store import JvSpatialStepStore, HarnessPersistenceError
from app.agentive.harness.contracts import PhysicalModelRequest
from app.agentive.harness.model_observations import persist_model_request_observation
from app.agentive.services.work_model_receipts import reconcile_retained_model_receipts
from app.api.errors import InsufficientPermissionsError, ResourceConflictError, ResourceNotFoundError
from app.services.attachment_storage import get_attachment_storage_service
from app.services.chat_attachment_review import read_chat_attachment_for_review
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
            attachment = data["attachment"]
            blob, mime = await read_chat_attachment_for_review(**attachment["scope"])
            assert mime == attachment["mime"]
            assert hashlib.sha256(blob).hexdigest() == attachment["sha256"]
            try:
                await read_chat_attachment_for_review(
                    **{**attachment["scope"], "user_id": attachment["foreign_user_id"]}
                )
            except (InsufficientPermissionsError, ResourceNotFoundError):
                pass
            else:
                raise AssertionError("Restored attachment accepted foreign scope")
            await get_attachment_storage_service().delete_attachment(attachment["storage_key"])
            try:
                await read_chat_attachment_for_review(**attachment["scope"])
            except ResourceConflictError:
                pass
            else:
                raise AssertionError("Restored metadata released a missing attachment")
            budget = data["budget"]
            reservation = (await database.get("object", budget["reservation_id"]))["context"]
            assert reservation["status"] == "reserved"
            assert reservation["dispatch_state"] == "intent"
            assert await reconcile_retained_model_receipts({}) == 0
            await persist_model_request_observation(
                PhysicalModelRequest.model_validate_json(budget["terminal"])
            )
            assert await reconcile_retained_model_receipts({}) == 1
            assert await reconcile_retained_model_receipts({}) == 0
            settled = (await database.get("object", budget["reservation_id"]))["context"]
            assert settled["status"] == "settled"
            root = (await database.get("object", budget["root_id"]))["context"]
            ledger = root["plan"]["mandate_budget"]
            assert ledger["reserved_units"] == 0
            assert ledger["charged_units"] == 4_000_000
            assert ledger["model_requests"] == 1
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
    source_storage = tmp_path / "source-storage"
    backup_storage = tmp_path / "backup-storage"
    restored_storage = tmp_path / "restored-storage"
    monkeypatch.setenv("JVSPATIAL_FILE_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("JVSPATIAL_FILES_ROOT_PATH", str(source_storage))
    monkeypatch.setattr(attachment_storage, "_ATTACHMENT_STORAGE_SERVICE", None)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, _ = await proposal()
        foreign_auth, _, _, _ = await proposal()
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
        thread = await create_thread(
            user_id=auth.id, provider_id="integral_native", workspace_id=workspace.id
        )
        await artifacts.upsert_artifact(
            user_id=auth.id,
            workspace_id=workspace.id,
            session_id=thread.id,
            key="restore-report",
            kind="document_draft",
            title="Restore rehearsal",
            body="Synthetic generated attachment for backup and restore.",
            metadata={"format": "markdown"},
            expected_version=0,
        )
        snapshot = await load_document_draft_snapshot(
            user_id=auth.id,
            workspace_id=workspace.id,
            thread_id=thread.id,
            key="restore-report",
            version=1,
        )
        attachment_id = await prepare_document_draft(snapshot)
        attachment_scope = {
            "user_id": auth.id,
            "workspace_id": workspace.id,
            "thread_id": thread.id,
            "attachment_id": attachment_id,
        }
        blob, mime, file_source = await inspect_chat_attachment_for_review(
            **attachment_scope
        )
        shutil.copytree(source_storage, backup_storage)

        root, payload, budget_db = await approved_root(mandate_payload())
        claimed = await claim_due_candidate(
            worker_id="restore-fixture", work_item_id=root.work_item_id
        )
        assert claimed is not None
        context = build_work_execution_context(
            work_item=claimed, logical_step_key="provider:0"
        )
        req = request(payload, effect=context.effect_key, cost="0.10")
        reservation, _, terminal = await marked_case(
            (root, None, claimed, context, req, budget_db)
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
                        "attachment": {
                            "scope": attachment_scope,
                            "foreign_user_id": foreign_auth.id,
                            "mime": mime,
                            "sha256": hashlib.sha256(blob).hexdigest(),
                            "storage_key": file_source["storage_key"],
                        },
                        "budget": {
                            "reservation_id": reservation.id,
                            "root_id": root.id,
                            "terminal": terminal.model_dump_json(),
                        },
                    }
                )
            )
            shutil.copytree(backup_storage, restored_storage)
            reader_env = env.copy()
            reader_env["JVSPATIAL_FILES_ROOT_PATH"] = str(restored_storage)
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
            source_hold = await postgres_raw_db.get("object", reservation.id)
            assert source_hold["context"]["status"] == "reserved"
            original, _, _ = await inspect_chat_attachment_for_review(
                **attachment_scope
            )
            assert original == blob
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
