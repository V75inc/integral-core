# Documentation maintenance

Maintain the reader guides, architecture decisions and implementation contracts against current source. Use stable filenames; keep chronological run logs and superseded review reports outside the maintained reader set. Git retains their history.

Before removing an old report, preserve unresolved requirements in the [hardening follow-up](../product/HARDENING_FOLLOW_UP.md). Record exact candidate checks and limits in the [release qualification](../ops/RELEASE_QUALIFICATION.md). Never promote historical checks into current release claims.

Regenerate API/capability inventories from source. Run `python3 scripts/check_documentation.py` after link changes and `python3 scripts/build_documentation.py` to inspect the local reader. The CSV files here describe the earlier editorial migration; they are historical provenance, not live implementation inventories.
