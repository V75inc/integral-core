"""Graph contiguousness gate (I-GRAPH-01 + I-GRAPH-02).

Phase 10.5 Plan 10.5-00 — substrate gate. Every persisted ``Node`` MUST
be reachable from ``Root → IntegralApp → …`` by walking named edges
(I-GRAPH-01). Records that legitimately don't benefit from graph
inclusion live as ``jvspatial.core.Object``, not ``Node`` (I-GRAPH-02);
this module asserts the I-GRAPH-01 half of the rule.

Three public helpers:

- ``GraphReachabilityWalker`` — `jvspatial.core.Walker` that visits every
  ``Node`` reachable from ``Root`` by following every registered edge.
  Accumulates ``set[node_id]`` per persisted ``Node`` class name.
- ``audit_orphans()`` — diff per-class persisted ids against the walker's
  reachable set; honors the allowlist file. Returns ``{class_name:
  [orphan_ids]}`` of *unlisted* orphans only.
- ``run_audit()`` — full audit producing an ``AuditReport`` with orphans,
  allowlisted_orphans, reachable_counts, and walker_runtime_seconds.
  Optionally raises ``GraphContiguousnessViolation`` on unlisted orphans.

Allowlist file (``backend/.ci/graph_contiguousness_allowlist.txt``)
grandfathered class names while reconciliation plans 10.5-01..08 drain
each class back onto the rooted subgraph. Plan 10.5-09 flips the gate
from advisory to blocking and the allowlist must be header-only by
then.

This is the first production walker in the codebase. It complements the
forward-compat ``CrossAppInboundReferenceWalker`` (Plan 10-06) and
follows the same canonical walker pattern documented in
``.planning/codebase/CONVENTIONS.md § Walker Pattern (Canonical)``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Set, Type

from jvspatial.core import Node, Walker
from jvspatial.core.decorators import on_visit
from jvspatial.core.entities import Root

logger = logging.getLogger(__name__)

# Allowlist lives at ``backend/.ci/graph_contiguousness_allowlist.txt``.
# This module sits at ``backend/app/services/graph_reachability.py`` →
# parents[2] resolves to ``backend/``.
ALLOWLIST_PATH = (
    Path(__file__).resolve().parents[2] / ".ci" / "graph_contiguousness_allowlist.txt"
)


class GraphContiguousnessViolation(Exception):
    """Raised when ``run_audit(raise_on_violation=True)`` sees unlisted orphans.

    The exception message enumerates every offending ``(class_name,
    [node_ids])`` pair so CI / startup-hook callers can surface the diff
    directly without re-running the audit.
    """


@dataclass
class AuditReport:
    """Structured output of ``run_audit``.

    Attributes:
        orphans: ``{class_name: [node_id]}`` for orphan nodes NOT covered
            by the allowlist. Empty when the gate is satisfied.
        allowlisted_orphans: same shape, but for classes intentionally
            grandfathered in ``graph_contiguousness_allowlist.txt``.
            Drains as plans 10.5-01..08 land.
        reachable_counts: ``{class_name: count}`` of nodes the walker
            reached, keyed by Python class name. Useful for CI summary
            rendering and progress dashboards.
        walker_runtime_seconds: ``perf_counter`` duration of the
            ``walker.spawn(root)`` call. Diagnostic only — no SLO.
    """

    orphans: dict[str, list[str]] = field(default_factory=dict)
    allowlisted_orphans: dict[str, list[str]] = field(default_factory=dict)
    reachable_counts: dict[str, int] = field(default_factory=dict)
    walker_runtime_seconds: float = 0.0


class GraphReachabilityWalker(Walker):
    """Walks every ``Node`` reachable from ``Root`` via any edge.

    Spawn from ``Root`` (or any sub-tree root for partial-graph audits).
    The ``@on_visit(Node)`` hook fires for every ``Node`` subclass since
    ``on_visit`` matches subclasses transitively. The walker queues
    every direct neighbor (``direction="both"``) so inbound + outbound
    edges are both followed — orphans on either side surface.

    Re-entrance / cycle protection is handled by jvspatial's
    ``TraversalProtection`` (records visited node ids; the explicit
    ``visited_ids`` set below additionally short-circuits per-Python-class
    bookkeeping so duplicate queue entries cost O(1) per node).

    State fields (Pydantic-tracked, transient):

    - ``visited_ids``: every node id the walker has seen at least once.
    - ``visited_by_class``: per-class-name accumulator of reachable ids.
      Keyed by ``type(here).__name__`` — direct match against
      ``Node.__subclasses__()`` introspection downstream.
    """

    visited_ids: Set[str] = set()
    visited_by_class: dict[str, Set[str]] = {}

    @on_visit(Node)
    async def visit_any(self, here: Node) -> None:
        """Record visit + enqueue neighbors regardless of node type."""
        if here.id in self.visited_ids:
            return
        self.visited_ids.add(here.id)
        cls_name = type(here).__name__
        self.visited_by_class.setdefault(cls_name, set()).add(here.id)
        try:
            neighbors = await here.nodes(direction="both")
        except Exception as e:
            # Defense-in-depth — a broken edge row should NOT halt the
            # audit. Log and continue from the next queued node.
            logger.warning(
                "GraphReachabilityWalker: nodes() raised on %s/%s — %s",
                cls_name,
                here.id,
                e,
            )
            return
        if neighbors:
            await self.visit(neighbors)


def _parse_allowlist(path: Optional[Path] = None) -> Set[str]:
    """Parse the allowlist file into ``{ClassName, …}``.

    Format: one class name per line. Lines starting with ``#`` and blank
    lines are ignored. Inline comments after the class name are
    tolerated (``UploadSession  # reason: orphan-pending-reconciliation``).

    ``path`` defaults to the module-level ``ALLOWLIST_PATH`` at CALL time
    so test monkey-patches of the module attribute take effect.
    """
    if path is None:
        path = ALLOWLIST_PATH
    if not path.exists():
        return set()
    classes: Set[str] = set()
    for raw_line in path.read_text().splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Strip trailing inline comment so ``ClassName  # reason`` parses.
        token = stripped.split("#", 1)[0].strip()
        if token:
            classes.add(token)
    return classes


def _all_node_classes() -> list[Type[Node]]:
    """Recursively enumerate every ``Node`` subclass registered at import time.

    ``Node.__subclasses__()`` only returns direct children; this helper
    walks the tree so deeply-nested classes (e.g. registry / branch nodes
    that inherit from ``Node`` directly under custom mixins) are caught.

    Sorted by class name for deterministic iteration in tests + reports.
    """
    seen: Set[Type[Node]] = set()
    stack: list[Type[Node]] = list(Node.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
    return sorted(seen, key=lambda c: c.__name__)


async def _persisted_ids(cls: Type[Node]) -> Set[str]:
    """Fetch every persisted id for ``cls`` via ``cls.find()``.

    A broken collection / index returns the empty set — the caller
    treats "no persisted rows" identically to "all rows reachable" so
    the audit short-circuits cleanly. A genuine bug in the underlying
    storage surfaces via the logged warning.
    """
    try:
        rows = await cls.find()  # type: ignore[attr-defined]
    except Exception as e:
        logger.warning("graph_reachability: %s.find() raised — %s", cls.__name__, e)
        return set()
    return {row.id for row in rows if getattr(row, "id", None)}


async def _walk_from_root() -> tuple["GraphReachabilityWalker", float]:
    """Spawn a fresh walker from Root; return (walker, elapsed_seconds)."""
    walker = GraphReachabilityWalker(visited_ids=set(), visited_by_class={})
    root: Optional[Node] = await Root.get(None)  # type: ignore[arg-type]
    t0 = time.perf_counter()
    await walker.spawn(root)
    return walker, time.perf_counter() - t0


async def audit_orphans(
    *, allowlist_path: Optional[Path] = None
) -> dict[str, list[str]]:
    """Return ``{class_name: [orphan_node_id]}`` for UNLISTED orphans.

    Allowlisted orphans are filtered out — callers that need to see them
    should use ``run_audit()`` for the full ``AuditReport``. Classes with
    zero persisted rows are skipped.

    ``allowlist_path`` defaults to ``ALLOWLIST_PATH`` resolved at CALL
    time so test monkey-patches take effect.
    """
    walker, _ = await _walk_from_root()
    allowlist = _parse_allowlist(allowlist_path)

    out: dict[str, list[str]] = {}
    for cls in _all_node_classes():
        if cls.__name__ in allowlist:
            continue
        persisted = await _persisted_ids(cls)
        if not persisted:
            continue
        reachable = walker.visited_by_class.get(cls.__name__, set())
        missing = persisted - reachable
        if missing:
            out[cls.__name__] = sorted(missing)
    return out


async def run_audit(*, raise_on_violation: bool = False) -> AuditReport:
    """Full audit producing an ``AuditReport``.

    Args:
        raise_on_violation: When ``True``, raises
            ``GraphContiguousnessViolation`` if any unlisted orphans
            exist. Plan 10.5-09 flips ``INTEGRAL_GRAPH_AUDIT_AT_STARTUP``
            consumers to ``raise_on_violation=True``; this plan ships the
            knob default OFF (advisory).

    Returns:
        ``AuditReport`` with orphans, allowlisted_orphans,
        reachable_counts, walker_runtime_seconds. The two orphan dicts
        always sum to the complete set of detached nodes — orphans is
        the "must fix" subset; allowlisted_orphans is the "in flight"
        subset for status / progress reporting.
    """
    walker, elapsed = await _walk_from_root()
    allowlist = _parse_allowlist()

    orphans: dict[str, list[str]] = {}
    allow_orphans: dict[str, list[str]] = {}
    reachable_counts: dict[str, int] = {
        cls_name: len(ids) for cls_name, ids in walker.visited_by_class.items()
    }

    for cls in _all_node_classes():
        persisted = await _persisted_ids(cls)
        if not persisted:
            continue
        reachable = walker.visited_by_class.get(cls.__name__, set())
        missing = persisted - reachable
        if not missing:
            continue
        if cls.__name__ in allowlist:
            allow_orphans[cls.__name__] = sorted(missing)
        else:
            orphans[cls.__name__] = sorted(missing)

    report = AuditReport(
        orphans=orphans,
        allowlisted_orphans=allow_orphans,
        reachable_counts=reachable_counts,
        walker_runtime_seconds=elapsed,
    )

    if raise_on_violation and orphans:
        details = ", ".join(f"{cls}={len(ids)}" for cls, ids in sorted(orphans.items()))
        raise GraphContiguousnessViolation(
            f"Unlisted orphan nodes detected (I-GRAPH-01): {details}. "
            f"Full id list: {orphans}"
        )
    return report


__all__ = [
    "ALLOWLIST_PATH",
    "AuditReport",
    "GraphContiguousnessViolation",
    "GraphReachabilityWalker",
    "audit_orphans",
    "run_audit",
]
