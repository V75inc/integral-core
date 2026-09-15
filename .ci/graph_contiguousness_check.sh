#!/usr/bin/env bash
# Graph Contiguousness drift guard (I-GRAPH-01).
#
# Runs the AST gate test from backend/tests/test_graph_contiguousness.py.
# Fails if any <Node>.create( site in backend/app/ lacks a same-function
# .connect( wire (or a known wiring-helper call, or an allowlist entry).
#
# See docs/INVARIANTS.md § I-GRAPH-01 + § I-GRAPH-02 for the contract.
# See backend/.ci/graph_contiguousness_allowlist.txt for the (header-
# only post-Phase 10.5 close) allowlist.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=resolve_python.sh
source "$REPO_ROOT/.ci/resolve_python.sh"

PYBIN="$(resolve_python "$REPO_ROOT")" || exit 1
cd "$REPO_ROOT/backend"

# Run ONLY the AST gate test — the runtime sweep needs a live DB and is
# exercised in the main pytest suite. The AST test is pure static
# analysis and runs in <2 seconds.
if ! "$PYBIN" -m pytest \
      "tests/test_graph_contiguousness.py::test_ast_gate_every_create_site_wires_edge" \
      -q --no-header --tb=line 2>&1; then
  echo ""
  echo "FAIL: graph contiguousness AST gate (I-GRAPH-01)."
  echo "  A <Node>.create(...) site in backend/app/ lacks a same-function"
  echo "  .connect(...) wire AND is not in backend/.ci/graph_contiguousness_allowlist.txt"
  echo "  AND is not a recognized wiring helper."
  echo ""
  echo "Options:"
  echo "  1. (preferred) Wire the structural edge in the same function:"
  echo "       await parent.connect(child, edge=HAS_FOO, attached_at=now)"
  echo "  2. If the new entity does NOT benefit from graph inclusion,"
  echo "     model it as jvspatial.core.Object instead (I-GRAPH-02)."
  echo "  3. If this is in-flight reconciliation work, add the class to"
  echo "     backend/.ci/graph_contiguousness_allowlist.txt with a reason"
  echo "     AND amend docs/INVARIANTS.md § I-GRAPH-01 in the same commit."
  exit 1
fi

exit 0
