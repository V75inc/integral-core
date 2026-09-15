"""Phase 5 Plan 05-02 — no-migration-path reject gate (MIG-03).

Cross-references the manifest-diff impact (which entry_types / fields would
break under the candidate manifest) against the manifest's declared
``migrations[].ops[]`` list. Returns the list of breaks with no declared
migration op — non-empty means HTTP 422 unless caller passes ``force=True``.

The impact payload shape (from
``app.services.content_profile_diff.compute_entry_impact_for_attached``) is
per-Track::

    {
      "track_id": "...",
      "total": <int>,
      "would_fail_validation": <int>,
      "would_need_migration": <int>,   # count of entries with field drift
      "sample_failing_ids": [...],
      "sample_failure_reasons": [{"entry_id": ..., "reason": ...}, ...],
    }

The reject gate's job is coarse: if ANY track reports
``would_need_migration > 0`` and the manifest declares ZERO migration ops,
the publish is rejected. When the manifest declares ANY migration ops, we
trust the author that the ops cover the breaks (a per-field reconciler is
out-of-scope for v1 — the impact payload does not carry per-field break
detail). Forced publish (``force=True``) bypasses this gate entirely.

This is a v1 conservative gate: it errs on the side of rejecting publishes
when migrations are missing. A future phase may refine it to a per-field
correlation once ``compute_entry_impact`` exposes the broken field names.
"""

from typing import Any, Dict, List


def detect_unhandled_breaks(
    impacts: List[Dict[str, Any]],
    migrations: List[Dict[str, Any]],
) -> List[str]:
    """Return descriptors of would_need_migration tracks with no declared op.

    Args:
        impacts: list of per-Track impact summaries from
            ``compute_entry_impact_for_attached``. Each entry has
            ``would_need_migration: int`` (count of entries with field drift).
        migrations: candidate_manifest["migrations"] — list of
            ``{from_version, to_version, ops: [{op, entry_type, ...}, ...]}``.

    Returns:
        List of human-readable break descriptors. Empty list means publish
        may proceed. Non-empty means HTTP 422 with
        ``details.unhandled_breaks`` unless caller passes ``force=True``.
    """
    # Count total declared ops across all migration entries.
    declared_op_count = 0
    declared_entry_types: set = set()
    for mig in migrations or []:
        for op in (mig or {}).get("ops") or []:
            if not isinstance(op, dict):
                continue
            kind = str(op.get("op") or "").strip()
            if not kind:
                continue
            declared_op_count += 1
            et = op.get("entry_type") or op.get("from_entry_type") or op.get("from")
            if et:
                declared_entry_types.add(str(et))

    unhandled: List[str] = []
    for impact in impacts or []:
        if not isinstance(impact, dict):
            continue
        need_count = int(impact.get("would_need_migration") or 0)
        fail_count = int(impact.get("would_fail_validation") or 0)
        if need_count == 0 and fail_count == 0:
            continue
        # If the manifest declared at least one migration op, trust the
        # author that ops cover the breaks (v1 conservative behaviour).
        # The author can always supply ``force=true`` to bypass.
        if declared_op_count > 0:
            continue
        track_id = impact.get("track_id") or "?"
        if need_count > 0:
            unhandled.append(
                f"track {track_id}: {need_count} entries need field "
                f"migration but manifest declares no migrations[].ops"
            )
        if fail_count > 0:
            unhandled.append(
                f"track {track_id}: {fail_count} entries would fail "
                f"validation under the candidate manifest with no migration op"
            )
    return unhandled
