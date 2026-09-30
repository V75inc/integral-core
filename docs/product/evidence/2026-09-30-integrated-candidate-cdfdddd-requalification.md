# Integrated candidate `cdfdddd` requalification

**Candidate SHA:** `cdfdddd2525e8ca4a6fa402bdd32990fea5c2b64`
**Integration baseline:** `main` at `abe1cedf480dc3a7a2861d8c28746f36074807ea`
**Qualification date:** 2026-09-30

This local candidate is `746400a690a18856b2e00b1c8dd0242cefefccab` plus PR #70's permission-cache generation fence (`2c42e5d65768307acf3e68369c5a7027553b3bcb`). It is an integrated code checkpoint, not a frozen release candidate. The full candidate diff was applied to a temporary Git index based on `main`, so staged-diff guards evaluated the 183-file integration diff without changing the worktree index.

## Qualification results

- `make verify` — **passed** on this SHA and the exact staged candidate diff. All 16 substrate guards, pre-commit hooks, pinned formatters, backend type/lint checks, frontend lint and type checks, Core wheel import, CI-faithful backend smoke, full backend suite, and frontend suite passed. The frontend suite reported **213 files and 1,274 tests passed**. Default backend skips were the declared Atlas integration, opt-in resolver benchmark, unseeded external App fixtures, and PostgreSQL-only tests covered by the next lane.
- `make test-postgres` — **passed** against the configured local PostgreSQL service. The PostgreSQL-mode suite completed; declared Atlas, benchmark, unseeded App, and end-to-end placeholder skips remain as reported by pytest.
- `make verify-independent-artifacts` — **passed**: Core wheel import, clean-install ASGI import, SDK wheel import, and signed external Asset Register load all succeeded. The run's generated SDK/App archives and signing identity were temporary and were not retained. The Core wheel built by the preceding `make verify` reported SHA-256 `2149f9382986bad838217bed2f26998377b4c05a4960c3a882003185d7dadaee`; a subsequent independent artifact run rebuilt the wheel with SHA-256 `2f893f9d8c662d7c6fb89c8363d59af2ff1cce8fe26573767593dedf8302481b`. These are build outputs, not yet a frozen release artifact identity.
- Authenticated browser smoke against the candidate API and frontend (`127.0.0.1:14011` and `127.0.0.1:19013`) used the disposable `integral_candidate_746` PostgreSQL fixture. Creating `C6 Candidate cdfdddd Track` showed the new Track immediately; an immediate page reload still showed it. Apps, Feed, Approvals, Background Tasks, Notifications, Settings, and Agent routes loaded. No model prompt was sent.

## What the browser recheck establishes

The preceding candidate `746400a` exposed a short stale-visibility window after Track creation: the Track persisted with its structural graph edges but was absent from the immediate reload and appeared after the permission-cache TTL. PR #70 adds a per-user generation fence so an in-flight pre-invalidation permission read cannot repopulate stale process-cache data. On this integrated SHA, the new Track remained visible after immediate reload. This closes that reproduced browser case for this candidate.

## Remaining C6 and release gates

This is not the complete C6 matrix. The browser smoke does not prove full browser acceptance for every wave, deployed transport parity, current-SHA Docker image identity, or registry image digests. The independent artifact checks passed, but exact SDK and signed App archive hashes plus the generated signing identity were not retained for this run. W0.3b external corpus custody/live configuration, W4.5 human usability acceptance, W5.2 migrated-App and human recommendation acceptance, and Product Owner architecture/release review remain open. No release is approved by this record.
