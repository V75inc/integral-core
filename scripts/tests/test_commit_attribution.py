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


if __name__ == "__main__":
    unittest.main()
