"""Focused tooling checks; no application runtime or credentials required."""

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "commit_attribution", ROOT / "scripts/commit_attribution.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AttributionTests(unittest.TestCase):
    def test_exact_agent_identities_and_case(self):
        for trailer in [
            b"Co-authored-by: Cursor <cursoragent@cursor.com>",
            b"Co-Authored-By: Claude Sonnet <noreply@anthropic.com>\r\n",
            b"co-authored-by: Another display name <CURSORAGENT@CURSOR.COM>",
            b"Co-authored-by: Agent <123+claude@users.noreply.github.com>",
            b"Co-authored-by: Agent <cursoragent@users.noreply.github.com>",
        ]:
            with self.subTest(trailer=trailer):
                self.assertIsNotNone(
                    MODULE.AGENT_TRAILER.search(b"Subject\n\n" + trailer)
                )

    def test_human_credit_and_non_trailer_text_are_preserved(self):
        for message in [
            b"Support Claude and Cursor integrations",
            b"Co-authored-by: Human <human@example.com>",
            b"Document cursoragent@cursor.com as an example",
            b"Quoted Co-authored-by: Cursor <cursoragent@cursor.com>",
            b"Co-authored-by: Human <noreply@anthropic.com.example.org>",
            b"Co-authored-by: Claude Human <claude@example.org>",
        ]:
            with self.subTest(message=message):
                self.assertIsNone(MODULE.AGENT_TRAILER.search(message))

    def test_hook_and_history_audit_are_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "Human",
                "GIT_AUTHOR_EMAIL": "human@example.com",
                "GIT_COMMITTER_NAME": "Human",
                "GIT_COMMITTER_EMAIL": "human@example.com",
            }

            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args], env=env)

            git("init", "-q")
            git(
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "--allow-empty",
                "-qm",
                "Human baseline",
            )
            git(
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "--allow-empty",
                "-qm",
                "Historical change\n\nCo-authored-by: Cursor <cursoragent@cursor.com>",
            )
            git(
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "--allow-empty",
                "-qm",
                "Descendant",
            )
            before = git("rev-parse", "HEAD")
            result = subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts/commit_attribution.py"),
                    "audit",
                    "HEAD",
                ],
                cwd=directory,
                capture_output=True,
                check=True,
            )
            report = json.loads(result.stdout)[0]
            self.assertEqual(
                (
                    report["commits"],
                    report["affected_messages"],
                    report["rewrite_commit_count"],
                ),
                (3, 1, 2),
            )
            self.assertEqual(git("rev-parse", "HEAD"), before)
            (Path(directory) / "scripts").mkdir()
            shutil.copy2(
                ROOT / "scripts/commit_attribution.py",
                Path(directory) / "scripts/commit_attribution.py",
            )
            message = Path(directory) / "message with spaces.txt"
            original = (
                b"Next change\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
            )
            message.write_bytes(original)
            blocked = subprocess.run(
                ["bash", str(ROOT / ".githooks/commit-msg"), str(message)],
                cwd=directory,
                capture_output=True,
            )
            self.assertEqual(blocked.returncode, 1)
            self.assertIn(b"Remove Cursor/Claude coauthor trailers", blocked.stderr)
            self.assertEqual(message.read_bytes(), original)
            message.write_bytes(
                b"Human change\n\nCo-authored-by: Human <human@example.com>\n"
            )
            allowed = subprocess.run(
                ["bash", str(ROOT / ".githooks/commit-msg"), str(message)],
                cwd=directory,
                check=False,
            )
            self.assertEqual(allowed.returncode, 0)
            self.assertEqual(git("rev-parse", "HEAD"), before)


class HistoryGateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Human",
            "GIT_AUTHOR_EMAIL": "human@example.com",
            "GIT_COMMITTER_NAME": "Human",
            "GIT_COMMITTER_EMAIL": "human@example.com",
        }
        self.git("init", "-q")
        self.commit("Human baseline")

    def git(self, *args, env=None):
        return (
            subprocess.check_output(
                ["git", "-C", self.directory.name, *args],
                env={**self.env, **(env or {})},
                stderr=subprocess.PIPE,
            )
            .decode()
            .strip()
        )

    def commit(self, message, env=None):
        self.git(
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--allow-empty",
            "-qm",
            message,
            env=env,
        )
        return self.git("rev-parse", "HEAD")

    def gate(self, *refs, event=None):
        command = ["python3", str(ROOT / "scripts/commit_attribution.py"), "check"]
        command.extend(refs or ("HEAD",))
        if event is not None:
            path = Path(self.directory.name) / "event.json"
            path.write_text(json.dumps(event))
            command.extend(("--event", str(path)))
        return subprocess.run(
            command,
            cwd=self.directory.name,
            env=self.env,
            capture_output=True,
        )

    def test_clean_human_history_and_provider_mentions_pass(self):
        self.commit(
            "Support Cursor and Claude; document noreply@anthropic.com\n\n"
            "Co-authored-by: Claude Human <claude@example.org>"
        )
        before = self.git("rev-parse", "HEAD")
        result = self.gate("HEAD", "HEAD", event={"pull_request": {"body": None}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["commits"], 2)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_attributed_ancestor_fails_even_when_tip_is_clean(self):
        bad = self.commit("Change\n\nCo-authored-by: Cursor <cursoragent@cursor.com>")
        self.commit("Clean descendant")
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            json.loads(result.stdout)["violations"],
            [{"commit": bad, "field": "coauthor"}],
        )

    def test_merge_cannot_reintroduce_old_attributed_branch(self):
        clean = self.git("rev-parse", "HEAD")
        self.git("checkout", "-qb", "old-history")
        bad = self.commit("Old work\n\nCo-authored-by: Claude <noreply@anthropic.com>")
        self.git("checkout", "-qb", "clean-main", clean)
        self.commit("Clean main work")
        self.git(
            "-c",
            "core.hooksPath=/dev/null",
            "merge",
            "--no-ff",
            "-qm",
            "Merge old work",
            "old-history",
        )
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            {"commit": bad, "field": "coauthor"},
            json.loads(result.stdout)["violations"],
        )

    def test_raw_author_and_committer_identities_are_checked(self):
        for role, email in (
            ("AUTHOR", "CURSORAGENT@CURSOR.COM"),
            ("COMMITTER", "noreply@anthropic.com"),
            ("AUTHOR", "123+claude@users.noreply.github.com"),
        ):
            with self.subTest(role=role, email=email):
                bad = self.commit("Change", {"GIT_" + role + "_EMAIL": email})
                # Mailmap aliases cannot hide the underlying attribution.
                (Path(self.directory.name) / ".mailmap").write_text(
                    f"Human <human@example.com> <{email}>\n"
                )
                result = self.gate()
                self.assertEqual(result.returncode, 1)
                self.assertIn(
                    {"commit": bad, "field": role.lower()},
                    json.loads(result.stdout)["violations"],
                )

    def test_pull_request_title_and_body_are_checked(self):
        for field in ("title", "body"):
            with self.subTest(field=field):
                result = self.gate(
                    event={
                        "pull_request": {
                            field: "Co-authored-by: Claude <noreply@anthropic.com>"
                        }
                    }
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    json.loads(result.stdout)["violations"],
                    [{"field": "pull_request." + field}],
                )

    def test_pull_request_text_is_data_and_other_events_pass(self):
        marker = Path(self.directory.name) / "must-not-exist"
        result = self.gate(
            event={
                "pull_request": {
                    "title": "Provider support",
                    "body": f"$(touch {marker})\n`touch {marker}`",
                }
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(self.gate(event={"merge_group": {}}).returncode, 0)

    def test_missing_ref_and_malformed_event_fail_closed(self):
        self.assertEqual(self.gate("missing-ref").returncode, 2)
        path = Path(self.directory.name) / "event.json"
        path.write_text("invalid json")
        result = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts/commit_attribution.py"),
                "check",
                "HEAD",
                "--event",
                str(path),
            ],
            cwd=self.directory.name,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)

    def test_shallow_history_fails_closed(self):
        self.commit("Second commit")
        with tempfile.TemporaryDirectory() as clone:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "-q",
                    "--depth=1",
                    Path(self.directory.name).as_uri(),
                    clone,
                ],
                check=True,
            )
            result = subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts/commit_attribution.py"),
                    "check",
                    "HEAD",
                ],
                cwd=clone,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn(b"Complete history is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
