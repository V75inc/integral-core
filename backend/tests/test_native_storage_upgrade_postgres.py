"""Reviewed native upgrades on disposable populated PostgreSQL databases."""

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from app.services.storage_indexes import native_upgrade as migration

pytestmark = pytest.mark.postgres


@unittest.skipUnless(
    os.getenv("STORAGE_INDEX_TEST_DSN"), "disposable PostgreSQL DSN required"
)
class ManagedMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg

        self.admin = await asyncpg.connect(os.environ["STORAGE_INDEX_TEST_DSN"])
        self.database = "native_migration_test_" + uuid.uuid4().hex[:12]
        await self.admin.execute(f'CREATE DATABASE "{self.database}"')
        parsed = urlsplit(os.environ["STORAGE_INDEX_TEST_DSN"])
        self.dsn = urlunsplit(parsed._replace(path="/" + self.database))
        self.conn = await asyncpg.connect(self.dsn)
        self.temp = tempfile.TemporaryDirectory(prefix="native-storage-test-")
        self.home = Path(self.temp.name)
        self.config = {
            "contract_version": 1,
            "deployment_profile": "desktop_single_user",
            "installation_id": "fixture-installation",
            "jwt_key": "fixture-old-key",
        }
        migration.write_private(self.home / "installation.json", self.config)
        self.attachment = self.home / "attachment.bin"
        self.attachment.write_bytes(b"\x00\xfforiginal attachment\x80")
        self.role = None
        self.addAsyncCleanup(self.cleanup)
        from app.services.storage_indexes.catalog import collection_sql

        for sql in collection_sql("public"):
            await self.conn.execute(sql)
        await self.conn.execute(
            "INSERT INTO public.object(id,entity,data) VALUES('retained','FixtureRecord',$1::jsonb)",
            json.dumps(
                {"context": {"body": "retained record", "attachment": "attachment.bin"}}
            ),
        )

    async def cleanup(self):
        await self.conn.close()
        await self.admin.execute(f'DROP DATABASE "{self.database}" WITH (FORCE)')
        if self.role:
            await self.admin.execute(f'DROP ROLE IF EXISTS "{self.role}"')
        await self.admin.close()
        self.temp.cleanup()

    async def plan(self):
        result = await migration.prepare(
            self.conn,
            self.home,
            "a" * 64,
            {
                "max_indexes": 2000,
                "max_index_bytes": 2_000_000_000,
                "max_build_bytes": 3_000_000_000,
            },
        )
        self.role = result["plan"]["runtime_role"]
        return result

    async def test_populated_interrupted_retry_is_restricted_and_preserves_data(self):
        import asyncpg

        envelope = await self.plan()
        original = await self.conn.fetchval(
            "SELECT data::text FROM object WHERE id='retained'"
        )
        parent = self.conn

        class InterruptedConnection:
            builds = 0

            def __getattr__(self, key):
                return getattr(parent, key)

            async def execute(self, sql, *args: object, **kwargs):
                if sql.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX")):
                    self.builds += 1
                    if self.builds == 3:
                        raise RuntimeError("injected interrupted index build")
                return await parent.execute(sql, *args, **kwargs)

        with self.assertRaisesRegex(RuntimeError, "injected interrupted"):
            await migration.apply_reviewed(
                InterruptedConnection(), self.dsn, self.home, envelope, "a" * 64
            )
        self.assertEqual(
            json.loads((self.home / "installation.json").read_text()), self.config
        )
        result = await migration.apply_reviewed(
            self.conn, self.dsn, self.home, envelope, "a" * 64
        )
        self.assertEqual(
            result,
            await migration.apply_reviewed(
                self.conn, self.dsn, self.home, envelope, "a" * 64
            ),
        )
        config = json.loads((self.home / "installation.json").read_text())
        self.assertEqual(
            {k: v for k, v in config.items() if not k.startswith("runtime_database_")},
            self.config,
        )
        self.assertEqual(
            await self.conn.fetchval(
                "SELECT data::text FROM object WHERE id='retained'"
            ),
            original,
        )
        self.assertEqual(
            self.attachment.read_bytes(), b"\x00\xfforiginal attachment\x80"
        )
        from app.services.storage_indexes.provision import initial_install

        resumed_dsn = await initial_install(
            self.dsn,
            runtime_role=config["runtime_database_user"],
            runtime_password=config["runtime_database_password"],
            plan_path=self.home / "storage-initial-plan.json",
        )
        self.assertFalse(
            (self.home / "storage-initial-plan.json").exists(),
            "startup must not create a new plan",
        )
        runtime = await asyncpg.connect(resumed_dsn)
        try:
            self.assertEqual(
                await runtime.fetchval(
                    "SELECT data::text FROM object WHERE id='retained'"
                ),
                original,
            )
            with self.assertRaises(asyncpg.InsufficientPrivilegeError):
                await runtime.execute("CREATE TABLE forbidden(id text)")
            with self.assertRaises(asyncpg.InsufficientPrivilegeError):
                await runtime.execute("DELETE FROM integral_index_catalog")
        finally:
            await runtime.close()

    async def test_wrong_runtime_or_changed_installation_fails_before_role_creation(
        self,
    ):
        envelope = await self.plan()
        with self.assertRaisesRegex(ValueError, "another home or runtime"):
            await migration.apply_reviewed(
                self.conn, self.dsn, self.home, envelope, "b" * 64
            )
        (self.home / "installation.json").write_text(
            json.dumps({**self.config, "runtime_database_user": self.role})
        )
        with self.assertRaisesRegex(ValueError, "Incomplete runtime credentials"):
            await migration.apply_reviewed(
                self.conn, self.dsn, self.home, envelope, "a" * 64
            )
        config = {**self.config, "jwt_key": "changed"}
        (self.home / "installation.json").write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "changed after migration review"):
            await migration.apply_reviewed(
                self.conn, self.dsn, self.home, envelope, "a" * 64
            )
        self.assertFalse(
            await self.conn.fetchval(
                "SELECT 1 FROM pg_roles WHERE rolname=$1", self.role
            )
        )
        self.assertFalse((self.home / "storage-upgrade-credential.json").exists())

    async def test_new_tcp_listener_preserves_reviewed_database_identity(self):
        class Listener:
            def __init__(self, conn, port):
                self.conn = conn
                self.port = port

            def __getattr__(self, name):
                return getattr(self.conn, name)

            async def fetchrow(self, query, *args):
                row = await self.conn.fetchrow(query, *args)
                # Windows pgserver gives the same database a new TCP port
                # whenever the private helper restarts.
                if "inet_server_port()" in query:
                    return {**dict(row), "host": "127.0.0.1", "port": self.port}
                return row

        original = self.conn
        self.conn = Listener(original, 52000)
        try:
            envelope = await self.plan()
            self.conn = Listener(original, 52001)
            from app.services.storage_indexes.inspection import target

            first = await target(Listener(original, 52000), "public")
            second = await target(self.conn, "public")
            self.assertNotEqual(first, second)
            receipt = await migration.apply_reviewed(
                self.conn, self.dsn, self.home, envelope, "a" * 64
            )
        finally:
            self.conn = original
        self.assertEqual(receipt["plan_hash"], envelope["plan"]["hash"])
        self.assertEqual(
            self.attachment.read_bytes(), b"\x00\xfforiginal attachment\x80"
        )
        self.assertEqual(
            await self.conn.fetchval(
                "SELECT count(*) FROM public.object WHERE id='retained'"
            ),
            1,
        )

    async def test_wrong_database_is_refused_before_creating_a_role(self):
        from app.services.storage_indexes.lifecycle import PlanError

        envelope = await self.plan()
        with self.assertRaisesRegex(PlanError, "Plan targets another database"):
            await migration.apply_reviewed(
                self.admin,
                os.environ["STORAGE_INDEX_TEST_DSN"],
                self.home,
                envelope,
                "a" * 64,
            )
        self.assertFalse(
            await self.admin.fetchval(
                "SELECT 1 FROM pg_roles WHERE rolname=$1", self.role
            )
        )
        self.assertFalse((self.home / "storage-upgrade-credential.json").exists())
        self.assertEqual(
            migration.read_private(self.home / "installation.json"), self.config
        )

    async def test_duplicate_credentials_fail_without_removing_records(self):
        import asyncpg

        from app.models.credentials import UserModelCredential

        for model in ("first", "second"):
            credential = UserModelCredential(user_id="synthetic-owner", model=model)
            exported = await credential.export()
            await self.conn.execute(
                "INSERT INTO public.object(id,entity,data) VALUES($1,$2,$3::jsonb)",
                credential.id,
                "UserModelCredential",
                json.dumps(exported),
            )
        original = await self.conn.fetch("SELECT id,data::text FROM object ORDER BY id")
        envelope = await self.plan()
        with self.assertRaises(asyncpg.UniqueViolationError):
            await migration.apply_reviewed(
                self.conn, self.dsn, self.home, envelope, "a" * 64
            )
        self.assertEqual(
            await self.conn.fetch("SELECT id,data::text FROM object ORDER BY id"),
            original,
        )
        self.assertEqual(
            migration.read_private(self.home / "installation.json"), self.config
        )

    async def test_unrelated_role_is_not_adopted(self):
        envelope = await self.plan()
        await self.conn.execute(f'CREATE ROLE "{self.role}" NOLOGIN')
        with self.assertRaisesRegex(ValueError, "Refusing to adopt"):
            await migration.apply_reviewed(
                self.conn, self.dsn, self.home, envelope, "a" * 64
            )
        self.assertFalse((self.home / "storage-upgrade-credential.json").exists())
        self.assertEqual(
            migration.read_private(self.home / "installation.json"), self.config
        )


if __name__ == "__main__":
    unittest.main()
