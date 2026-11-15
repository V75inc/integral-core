"""Focused source-composition qualification; no installed frontend needed."""

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from distribution import (
    GUIDE_CHECK,
    GUIDE_INPUTS,
    SDK_REFERENCES,
    SUPPORT,
    compose,
    snapshot,
)


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

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node required")
    def test_portable_snapshot_retains_guide_gate_without_parent_writes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            core = root / "core"
            snapshot(core)
            descriptor = json.loads((core / "core-source.json").read_text())
            for name in GUIDE_INPUTS:
                self.assertIn(f"build-support/{name}", descriptor["files"])
            # Compose into an unrelated parent, then remove the source snapshot:
            # no repository or sibling SDK may be required by the build gate.
            parent = root / "isolated"
            parent.mkdir()
            sentinel = parent / "AGENTS.md"
            sentinel.write_text("Do not overwrite parent instructions")
            output = parent / "frontend"
            compose(core, output, None, "example")
            self.assertEqual(
                (output / "package-lock.json").read_bytes(),
                (core / "frontend/package-lock.json").read_bytes(),
            )
            for name, (old, new) in SDK_REFERENCES.items():
                content = (output / name).read_text()
                self.assertNotIn(old, content)
                self.assertIn(new, content)
            vitest = (output / "vitest.config.ts").read_text()
            self.assertIn(f"exclude: ['{SUPPORT}/**', ", vitest)
            self.assertIn("'**/node_modules/**'", vitest)
            self.assertIn("'**/domain_apps/**'", vitest)
            sdk_entry = output / SUPPORT / "sdk/react/src/index.tsx"
            self.assertEqual(
                sdk_entry.read_bytes(),
                (core / "build-support/sdk/react/src/index.tsx").read_bytes(),
            )
            self.assertTrue((sdk_entry.parent / "app-patterns.ts").is_file())
            shutil.rmtree(core)
            result = subprocess.run(
                ["npm", "run", "check:app-ui-guides"],
                cwd=output,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(set(parent.iterdir()), {output, sentinel})
            self.assertEqual(
                sentinel.read_text(), "Do not overwrite parent instructions"
            )
            guide = output / SUPPORT / "APP_UI_GUIDE.md"
            guide.write_text(guide.read_text() + "\nSTALE\n")
            result = subprocess.run(
                ["npm", "run", "check:app-ui-guides"],
                cwd=output,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("STALE", result.stderr)

    def test_missing_guide_support_refused_before_output(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            core = self.fixture(root)
            package = core / "frontend/package.json"
            package.write_text(
                json.dumps({"scripts": {"check:app-ui-guides": GUIDE_CHECK}})
            )
            descriptor = json.loads((core / "core-source.json").read_text())
            descriptor["files"]["frontend/package.json"] = hashlib.sha256(
                package.read_bytes()
            ).hexdigest()
            descriptor["source_tree_sha256"] = hashlib.sha256(
                json.dumps(descriptor["files"], sort_keys=True).encode()
            ).hexdigest()
            (core / "core-source.json").write_text(json.dumps(descriptor))
            with self.assertRaisesRegex(
                ValueError, "missing App UI guide build inputs"
            ):
                compose(core, root / "out", None, "example")
            self.assertFalse((root / "out").exists())

    def test_tampered_guide_input_refused_before_output(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            core = root / "core"
            snapshot(core)
            (core / "build-support/sdk/react/bin/app-ui-guides.mjs").write_text(
                "bypass"
            )
            with self.assertRaises(ValueError):
                compose(core, root / "out", None, "example")
            self.assertFalse((root / "out").exists())

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
