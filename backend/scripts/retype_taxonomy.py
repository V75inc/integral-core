"""Phase 16 ACC-06 — Re-type legacy Contacts / Opportunities entries.

Idempotent one-shot script. For every standalone ``crm`` App (Phase 31),
classifies each legacy Contacts entry (``IS_OF_TYPE → contact``) onto
the right new EntryType (``account`` / ``lead`` / ``contact_person`` /
``partner``) and each legacy Opportunities entry (``IS_OF_TYPE →
opportunity``) onto ``bid`` or ``prospect`` based on its ``stage``
field. Re-points the IS_OF_TYPE edge accordingly via existing Node CRUD.

Heuristics (deterministic, best-effort):

  Contacts → Account|Lead|Contact Person|Partner
    - custom_fields.stage == "lead" OR title contains "lead"  → lead
    - custom_fields.company present AND title looks like an org name → account
    - title contains "partner" / "vendor"                      → partner
    - default                                                  → contact_person

  Opportunities → Bid|Prospect
    - custom_fields.stage in {negotiation, won, lost}          → bid
    - custom_fields.stage in {lead, prospect, qualified, ""}    → prospect
    - default                                                  → prospect

Prints a per-entry classification report so the architect can spot-check
borderline cases. Safe to run against an empty DB.

Usage::

    python -m scripts.retype_taxonomy --dry-run
    python -m scripts.retype_taxonomy
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.models.edges import IS_OF_TYPE
from app.models.nodes import App, Entry, EntryType, Track

logger = logging.getLogger(__name__)

CRM_APP_SLUG = "crm"
ORG_TOKENS = (
    " inc",
    " llc",
    " ltd",
    " corp",
    " co.",
    " gmbh",
    " ab",
    " sa",
    " holdings",
    "company",
    "industries",
)


def _classify_contact(entry: Entry) -> str:
    custom = getattr(entry, "custom_fields", {}) or {}
    title = (getattr(entry, "title", "") or "").strip().lower()
    stage = str(custom.get("stage") or "").lower()
    if stage == "lead" or "lead" in title:
        return "lead"
    if any(t in title for t in ORG_TOKENS):
        return "account"
    if "partner" in title or "vendor" in title or "reseller" in title:
        return "partner"
    return "contact_person"


def _classify_opportunity(entry: Entry) -> str:
    custom = getattr(entry, "custom_fields", {}) or {}
    stage = str(custom.get("stage") or "").lower()
    if stage in {"negotiation", "won", "lost"}:
        return "bid"
    return "prospect"


async def _find_crm_apps() -> List[App]:
    out = []
    for app in await App.find():
        if (getattr(app, "attached_operational_model_slug", "") or "") == CRM_APP_SLUG:
            out.append(app)
    return out


async def _find_track_by_key(app: App, track_key: str) -> Optional[Track]:
    tracks = await app.nodes(edge=["CONTAINS"], direction="out", node=["Track"])
    for t in tracks:
        if (
            isinstance(t, Track)
            and (getattr(t, "track_template_key", "") or "") == track_key
        ):
            return t
    return None


async def _find_entry_type_in_track(track: Track, key: str) -> Optional[EntryType]:
    # EntryType is reachable via Track → OperationalModel → CONTAINS → EntryType
    cps = await track.nodes(
        edge=["HAS_OPERATIONAL_MODEL"], direction="out", node=["OperationalModel"]
    )
    for cp in cps:
        ets = await cp.nodes(edge=["CONTAINS"], direction="out", node=["EntryType"])
        for et in ets:
            if isinstance(et, EntryType) and (getattr(et, "key", "") or "") == key:
                return et
    return None


async def _current_entry_type_key(entry: Entry) -> str:
    ets = await entry.nodes(edge=["IS_OF_TYPE"], direction="out", node=["EntryType"])
    for et in ets:
        return str(getattr(et, "key", "") or "")
    return ""


async def _retype_entry(
    *,
    entry: Entry,
    new_et: EntryType,
    dry_run: bool,
) -> None:
    ctx = await entry.get_context()
    # Drop existing IS_OF_TYPE edges.
    old_ets = await entry.nodes(
        edge=["IS_OF_TYPE"], direction="out", node=["EntryType"]
    )
    for ot in old_ets:
        old_edges = await ctx.find_edges_between(entry.id, ot.id, edge_class=IS_OF_TYPE)
        for ed in old_edges:
            if dry_run:
                continue
            try:
                await ed.delete()
            except Exception as exc:  # pragma: no cover
                logger.warning("IS_OF_TYPE delete failed (continuing): %s", exc)
    if dry_run:
        return
    now = datetime.now(timezone.utc).isoformat()
    await entry.connect(new_et, edge=IS_OF_TYPE, assigned_at=now)
    # Update denormalized scalar cache.
    setattr(entry, "type_id", new_et.id)
    try:
        await entry.save()
    except Exception as exc:  # pragma: no cover
        logger.warning("Entry.save after retype failed: %s", exc)


async def retype(*, dry_run: bool = False) -> Dict[str, object]:
    stats = {
        "crm_apps": 0,
        "contacts_classified": {
            "lead": 0,
            "account": 0,
            "partner": 0,
            "contact_person": 0,
            "skipped": 0,
        },
        "opportunities_classified": {"bid": 0, "prospect": 0, "skipped": 0},
        "retypes_applied": 0,
        "would_retype": 0,
        "no_change": 0,
        "missing_entry_type": 0,
    }
    report: List[Tuple[str, str, str, str]] = []
    apps = await _find_crm_apps()
    stats["crm_apps"] = len(apps)
    if not apps:
        logger.info("retype_taxonomy: no CRM Apps found; no-op")
        stats["report"] = report
        return stats
    for app in apps:
        contacts_track = await _find_track_by_key(app, "contacts")
        if contacts_track is not None:
            entries = await contacts_track.nodes(
                edge=["CONTAINS"], direction="out", node=["Entry"]
            )
            for entry in entries:
                if not isinstance(entry, Entry):
                    continue
                current = await _current_entry_type_key(entry)
                if current != "contact":
                    # Already retyped or never a legacy `contact`.
                    stats["contacts_classified"]["skipped"] += 1
                    continue
                target_key = _classify_contact(entry)
                stats["contacts_classified"][target_key] += 1
                new_et = await _find_entry_type_in_track(contacts_track, target_key)
                if new_et is None:
                    stats["missing_entry_type"] += 1
                    logger.warning(
                        "retype: no EntryType %r in contacts track %s; skipping entry %s",
                        target_key,
                        contacts_track.id,
                        entry.id,
                    )
                    continue
                report.append(("contact", str(entry.id), str(entry.title), target_key))
                if dry_run:
                    stats["would_retype"] += 1
                else:
                    await _retype_entry(entry=entry, new_et=new_et, dry_run=False)
                    stats["retypes_applied"] += 1
        opps_track = await _find_track_by_key(app, "opportunities")
        if opps_track is not None:
            entries = await opps_track.nodes(
                edge=["CONTAINS"], direction="out", node=["Entry"]
            )
            for entry in entries:
                if not isinstance(entry, Entry):
                    continue
                current = await _current_entry_type_key(entry)
                if current != "opportunity":
                    stats["opportunities_classified"]["skipped"] += 1
                    continue
                target_key = _classify_opportunity(entry)
                stats["opportunities_classified"][target_key] += 1
                new_et = await _find_entry_type_in_track(opps_track, target_key)
                if new_et is None:
                    stats["missing_entry_type"] += 1
                    logger.warning(
                        "retype: no EntryType %r in opportunities track %s; "
                        "skipping entry %s",
                        target_key,
                        opps_track.id,
                        entry.id,
                    )
                    continue
                report.append(
                    ("opportunity", str(entry.id), str(entry.title), target_key)
                )
                if dry_run:
                    stats["would_retype"] += 1
                else:
                    await _retype_entry(entry=entry, new_et=new_et, dry_run=False)
                    stats["retypes_applied"] += 1
    stats["report"] = report
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = asyncio.run(retype(dry_run=args.dry_run))
    print("retype_taxonomy stats:")
    for k, v in stats.items():
        if k == "report":
            continue
        print(f"  {k}: {v}")
    report = stats.get("report") or []
    if report:
        print("classification report:")
        for source_type, entry_id, title, target in report:
            print(f"  {source_type:13s} {entry_id} '{title}' -> {target}")


if __name__ == "__main__":
    main()
