#!/usr/bin/env python3
"""Self-test the guard on disposable runtime source fixtures."""

from contextlib import redirect_stdout
import io
import tempfile
from pathlib import Path
import unittest

from unittest.mock import patch

import app_boundary_guard as guard
from app_boundary_guard import collect


class BoundaryGuardTest(unittest.TestCase):
    def test_domain_logic_rejected_but_comments_and_generic_schema_not_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backend = root / "backend/app"
            frontend = root / "frontend/src"
            backend.mkdir(parents=True)
            frontend.mkdir(parents=True)
            (backend / "app.py").write_text(
                '# from hr_host import HR\n'
                'from hr_host.forms import view\n'
                'route = "/me/onboarding-form"\n'
                'generic = "form_default_group"\n'
            )
            (frontend / "Form.tsx").write_text(
                '// const notBehavior = "seed_from";\n'
                'const hook = "seed_from";\n'
                'const route = "/me/onboarding-form";\n'
                'const title = <p>Employment Contract</p>;\n'
                'const other = "form_readonly_groups";\n'
            )
            found = collect(root)
            self.assertEqual(sum(found.values()), 5)
            self.assertIn(("backend/app/app.py", "app-import", "hr_host.forms"), found)
            self.assertIn(("frontend/src/Form.tsx", "implicit-field", "seed_from"), found)


    def test_main_rejects_domain_logic_without_exceptions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            area = root / "frontend/src"
            area.mkdir(parents=True)
            source = area / "Form.tsx"
            source.write_text("const item = row.contract_status;")
            with patch.object(guard, "ROOT", root):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    self.assertEqual(guard.main(), 1)
                self.assertIn("named-App dependency", stdout.getvalue())
                source.write_text("const item = row.general_status;")
                self.assertEqual(guard.main(), 0)

    def test_generic_dynamic_package_namespace_is_not_concrete_app_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            area = root / "backend/app"
            area.mkdir(parents=True)
            (area / "loader.py").write_text('module = f"app.packages.{slug}.{handler}"')
            self.assertFalse(collect(root))
            (area / "loader.py").write_text('module = "app.packages.specific_hr.tools"')
            self.assertTrue(collect(root))

    def test_sdk_is_subject_to_same_runtime_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            area = root / "sdk/react/src"
            area.mkdir(parents=True)
            (area / "adapter.ts").write_text('export const key = "employee_ids";')
            self.assertIn(
                ("sdk/react/src/adapter.ts", "implicit-field", "employee_ids"),
                collect(root),
            )

    def test_concrete_installed_tool_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            area = root / "backend/app/services"
            area.mkdir(parents=True)
            (area / "dispatch.py").write_text('tool = "e_sign_vault_save"')
            self.assertIn(
                ("backend/app/services/dispatch.py", "named-vault-tool", "e_sign_vault_save"),
                collect(root),
            )

    def test_shipped_plugin_runtime_is_not_exempt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            area = root / "backend/app/plugins/renderer"
            area.mkdir(parents=True)
            (area / "__init__.py").write_text('policy = "hr_app"')
            self.assertTrue(collect(root))


if __name__ == "__main__":
    unittest.main()
