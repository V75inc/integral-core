"""Focused source-composition qualification; no installed frontend needed."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from distribution import compose


class DistributionContract(unittest.TestCase):
    def fixture(self, root):
        core = root / "core"
        (core / "frontend/src/host").mkdir(parents=True)
        (core / "frontend/package-lock.json").write_text("{}")
        (core / "frontend/src/host/registry.ts").write_text("neutral registry")
        files = {
            p.relative_to(core).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in core.rglob("*")
            if p.is_file()
        }
        identity = hashlib.sha256(
            json.dumps(files, sort_keys=True).encode()
        ).hexdigest()
        (core / "core-source.json").write_text(
            json.dumps(
                {
                    "contract_version": 1,
                    "source_commit": "baseline",
                    "source_tree_sha256": identity,
                    "files": files,
                }
            )
        )
        return core

    def test_independent_host_composition(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            core = self.fixture(root)
            overlay = root / "overlay"
            overlay.mkdir()
            (overlay / "register.tsx").write_text("host registration")
            compose(core, root / "out", overlay, "example")
            self.assertEqual(
                (root / "out/src/host/registry.ts").read_text(), "neutral registry"
            )
            self.assertTrue((root / "out/distribution-build.json").exists())

    def test_modified_or_extra_source_refused_before_output(self):
        for extra in (True, False):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                core = self.fixture(root)
                target = core / ("extra.txt" if extra else "frontend/package-lock.json")
                target.write_text("tampered")
                with self.assertRaises(ValueError):
                    compose(core, root / "out", None, "example")
                self.assertFalse((root / "out").exists())

    def test_registry_and_symlink_overlay_refused(self):
        for symlink in (True, False):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                core = self.fixture(root)
                overlay = root / "overlay"
                overlay.mkdir()
                if symlink:
                    (overlay / "linked").symlink_to(core / "frontend/package-lock.json")
                else:
                    (overlay / "registry.ts").write_text("replace authority")
                with self.assertRaises(ValueError):
                    compose(core, root / "out", overlay, "example")
                self.assertFalse((root / "out").exists())

    def test_session_contract_overlay_refused(self):
        """A product supplies an implementation, never a replacement contract."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            core = self.fixture(root)
            overlay = root / "overlay"
            overlay.mkdir()
            (overlay / "session-contract.ts").write_text("replace authority contract")
            with self.assertRaises(ValueError):
                compose(core, root / "out", overlay, "example")
            self.assertFalse((root / "out").exists())


if __name__ == "__main__":
    unittest.main()
