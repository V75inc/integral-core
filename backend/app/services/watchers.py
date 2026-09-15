"""Watch-edge writer — the I-CRUD-01 drain's smallest whole unit.

``POST /tracks/{id}/watch`` and ``POST /entries/{id}/watch`` each carried an
identical inline ``user.connect(target, edge=WATCHES)`` — two copies of one
graph write living in ``api/``, which is exactly the drift the service-layer
ratchet (`.ci/service_layer_drift_check.sh`) exists to drain. Both handlers now
route through here; the corresponding allowlist entries are gone and the
ratchet ceiling is lower.

Idempotency lives here with the write, not in the callers: watching twice must
not stack ``WATCHES`` edges (the watcher lists and counts would double), and an
invariant enforced in two handlers is an invariant one of them eventually
drops.

I-GRAPH-01 note: ``WATCHES`` is an associative edge between two ALREADY-rooted
nodes — no node is created here, so there is no structural-edge obligation to
carry.
"""

from __future__ import annotations

from app.models.edges import WATCHES
from app.utils.time import utc_now_iso


async def ensure_watch_edge(user_node, target) -> bool:
    """Idempotently wire ``user —WATCHES→ target``.

    Returns True when an edge was created, False when one already existed.
    ``target`` is any watchable node (Track, Entry); the caller has already
    resolved permissions — this is the write, not the gate.
    """
    ctx = await target.get_context()
    existing = await ctx.find_edges_between(user_node.id, target.id, edge_class=WATCHES)
    if existing:
        return False
    await user_node.connect(target, edge=WATCHES, watched_at=utc_now_iso())
    return True
