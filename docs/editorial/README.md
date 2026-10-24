# Documentation maintenance

Maintain one current guide per topic, backed by source and executable tests.
The [documentation hub](../README.md) provides reader navigation; architecture
decisions retain rationale, and [invariants](../INVARIANTS.md) preserve boundaries.
Update affected guides when behavior changes instead of adding a second plan
or a chronological task log. Regenerate source-derived inventories.

Retire completed plans, superseded reviews, raw screenshots and run logs from
the reader tree. Git preserves their history. Carry unresolved requirements into
[hardening follow-up](../product/HARDENING_FOLLOW_UP.md); keep concise exact-candidate
results in [release qualification](../ops/RELEASE_QUALIFICATION.md). CI and PR
artifacts hold detailed execution evidence. Never turn historical checks into
current release claims.

Run `python3 scripts/check_documentation.py` after link changes and
`python3 scripts/build_documentation.py` to inspect the reader. Keep local drafts,
provider credentials, recovery snapshots and runtime data outside tracked docs.
