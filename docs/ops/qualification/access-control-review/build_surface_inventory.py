"""Build a passive source inventory; this is not enforcement qualification."""

import ast
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DESTINATION = Path(__file__).with_name("surface_inventory.json")


def inventory():
    surfaces = []
    hashes = {}
    files = subprocess.check_output(
        ["rg", "--files", "backend/app/api"], cwd=ROOT, text=True
    ).splitlines()
    facades = {
        "backend/app/services/hooks/registry.py": "ToolContext",
        "backend/app/services/app_operations/context.py": "OperationContext",
    }
    for filename in sorted(set(files) | set(facades)):
        if not filename.endswith(".py"):
            continue
        source = (ROOT / filename).read_bytes()
        tree = ast.parse(source, filename=filename)
        hashes[filename] = hashlib.sha256(source).hexdigest()
        if filename in facades:
            cls = next(
                item
                for item in tree.body
                if isinstance(item, ast.ClassDef) and item.name == facades[filename]
            )
            for item in cls.body:
                if isinstance(item, ast.AsyncFunctionDef):
                    surfaces.append(
                        {
                            "kind": "facade_declaration",
                            "callable": cls.name + "." + item.name,
                            "file": filename,
                            "line": item.lineno,
                            "qualification": "unmapped",
                            "evidence": [],
                        }
                    )
        else:
            for item in ast.walk(tree):
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in item.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    if ast.unparse(decorator.func).split(".")[-1] != "endpoint":
                        continue
                    keywords = {k.arg: ast.unparse(k.value) for k in decorator.keywords}
                    surfaces.append(
                        {
                            "kind": "rest_declaration",
                            "callable": item.name,
                            "path_expression": (
                                ast.unparse(decorator.args[0])
                                if decorator.args
                                else keywords.get("path", "unspecified")
                            ),
                            "methods_expression": keywords.get(
                                "methods", "framework_default"
                            ),
                            "auth_expression": keywords.get(
                                "auth", "framework_default"
                            ),
                            "file": filename,
                            "line": item.lineno,
                            "qualification": "unmapped",
                            "evidence": [],
                        }
                    )
    return {
        "schema_version": 1,
        "status": "source_inventory_not_qualification",
        "limits": [
            "Declarations do not prove reachable registration, enforcement or surface parity.",
            "Facade counts include overrides; this is a declaration inventory.",
            "Worker, event, public, streaming, file and imported helper surfaces require additional inventory.",
            "Map each relevant surface to use cases and paired runtime evidence before closure.",
        ],
        "source_sha256": hashes,
        "surfaces": surfaces,
    }


if __name__ == "__main__":
    data = inventory()
    DESTINATION.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Wrote {len(data['surfaces'])} declarations to {DESTINATION}")
