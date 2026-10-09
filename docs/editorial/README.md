# Documentation renewal record

This edition replaces the maintained reader journey with a current introduction, extended white paper, user guide, and focused technical references. It was authored from the staging checkout on 8 October 2026; concurrent local implementation changes were preserved.

## Disposition

The audit covered 233 tracked Markdown documents: 224 under docs and nine repository READMEs. Of these, 69 were rewritten, 162 retired, one generated capability document preserved, and the invariant catalog edited without dropping active IDs. New guides supplement the surviving paths.

The [document disposition table](document-disposition.csv) records every original path and its current reference. A replacement explains where current guidance lives; it does not mean all historical acceptance evidence was carried into a present release claim.

Historical plans, sprint diaries, old candidates, duplicated decisions, and obsolete architecture were removed from maintained docs. Fifty-nine associated evidence assets were retired. Forty-one YAML qualification fixtures were moved unchanged into backend/tests/fixtures/qualification/contracts, with their readers updated. They remain machine test inputs, not current release evidence.

## Contracts preserved

All 84 active invariant IDs remain. Detailed role, unstaged-write, native-session, and physical-dispatch authority clauses were retained during editing. Repeated milestone summaries and retired fabric narratives were removed. Current corrections include always-on agentive boot, catalog-driven authoring, the nine-point hook catalog, YAML/runtime version separation, and scoped conflict access.

Generated capability artifacts and approved brand geometry remain source-controlled. Architecture decisions preserve their implementation/proposal distinctions; contributed-skill marketplace and wider composition designs are not promoted to completed features.

## Evidence discipline

Claims were checked against package metadata, settings, models/edges, request scope, permissions, public SDK types, package loading, hooks, active definitions, native session/work services, view manifests, and relevant tests. The [claim/source map](claim-sources.csv) provides the principal pointers. The API reference is a source declaration inventory, not a deployment acceptance result.

A local snapshot of the originals and its SHA-256 were saved outside the repository before deletion. Git retains tracked history. Public documentation contains no snapshot credentials or private deployment environment.

## Local review checks

Markdown path and anchor checks passed across 85 files. The local reader's 1,401 internal links and asset references passed verification, and the introduction, white paper, and guide were inspected in the browser. All 41 relocated fixtures match the originals byte for byte; all 84 active invariant IDs are retained.

The targeted backend run completed with 35 passed and one failure in `test_I_APP_03_label_field_resolver_is_single_source`. Its three reported `label_field` occurrences in the region-system plugin and migration service also exist at the branch's unchanged HEAD. This is an existing architecture-guard failure, not a documentation acceptance pass or a qualified release. The documentation scripts pass formatting and lint checks.

## Maintenance

Use the current guides and contracts rather than adding another parallel plan. Regenerate source-derived artifacts. Keep machine fixtures separate from reader documentation. Run scripts/check_documentation.py after editing links or consolidating paths. Browser-readable review pages can be built with scripts/build_documentation.py; they are local artifacts until publication is authorized.
