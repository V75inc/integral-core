"""Conservative authority-writer candidates; declarations are not qualification."""

import ast
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DESTINATION = Path(__file__).with_name("grant_writer_inventory.json")
FAMILIES = {"IS_MEMBER_OF", "COLLABORATES_ON", "OWNS"}
RAW_WRITES = {"save", "delete", "find_one_and_update", "insert_if_absent"}
COMMANDS = {
    "apply_invitation_grant",
    "record_grant_change",
    "ensure_guest_membership",
    "add_collaborator",
    "update_collaborator_role",
    "remove_collaborator",
    "update_workspace_member",
    "add_workspace_member",
    "remove_workspace_member",
    "depart_workspace",
    "provision_workspace_member",
}


class Candidates(ast.NodeVisitor):
    def __init__(self, filename, source):
        self.filename = filename
        self.source = source
        self.function = "<module>"
        self.rows = []

    def visit_AsyncFunctionDef(self, node):
        prior = self.function
        self.function = node.name
        self.generic_visit(node)
        self.function = prior

    visit_FunctionDef = visit_AsyncFunctionDef

    def visit_Call(self, node):
        name = ast.unparse(node.func).split(".")[-1]
        family = None
        kind = None
        if name == "connect":
            for keyword in node.keywords:
                if keyword.arg == "edge":
                    candidate = ast.unparse(keyword.value).split(".")[-1]
                    if candidate in FAMILIES:
                        family, kind = candidate, "typed_authority_connect"
        elif name == "ensure_edge":
            for argument in node.args:
                candidate = ast.unparse(argument).split(".")[-1]
                if candidate in FAMILIES:
                    family, kind = candidate, "typed_authority_upsert"
        elif name in RAW_WRITES and node.args:
            if isinstance(node.args[0], ast.Constant) and node.args[0].value == "edge":
                kind = "raw_edge_write_candidate"
        elif name in COMMANDS:
            kind = "delegated_authority_command_candidate"
        if kind:
            self.rows.append(
                {
                    "file": self.filename,
                    "function": self.function,
                    "line": node.lineno,
                    "kind": kind,
                    "family": family,
                    "call": ast.unparse(node),
                    "review_status": "unmapped",
                    "evidence": [],
                }
            )
        self.generic_visit(node)


def inventory():
    files = subprocess.check_output(
        ["rg", "--files", "backend/app"], cwd=ROOT, text=True
    ).splitlines()
    rows, hashes = [], {}
    for filename in sorted(files):
        if (
            not filename.endswith(".py")
            or "/packages/" in filename
            or "/plugins/" in filename
        ):
            continue
        source = (ROOT / filename).read_bytes()
        visitor = Candidates(filename, source)
        visitor.visit(ast.parse(source, filename=filename))
        if visitor.rows:
            hashes[filename] = hashlib.sha256(source).hexdigest()
            rows.extend(visitor.rows)
    return {
        "schema_version": 1,
        "status": "source_candidates_not_qualification",
        "limits": [
            "Raw writes may be locks, histories or unrelated edges; each candidate needs semantic review.",
            "Dynamic edge families, framework/bootstrap writers, helper aliases, imports/restores and domain producers require additional inventory.",
            "No claim of exhaustive writers, runtime registration, fence/history convergence or tested enforcement.",
        ],
        "source_sha256": hashes,
        "candidates": rows,
    }


if __name__ == "__main__":
    data = inventory()
    DESTINATION.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Wrote {len(data['candidates'])} candidates to {DESTINATION}")
