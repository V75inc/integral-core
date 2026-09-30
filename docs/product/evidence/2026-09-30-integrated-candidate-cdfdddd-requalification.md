# Integrated candidate `cdfdddd` requalification

**Candidate SHA:** `cdfdddd2525e8ca4a6fa402bdd32990fea5c2b64`
**Integration baseline:** `main` at `abe1cedf480dc3a7a2861d8c28746f36074807ea`
**Qualification date:** 2026-09-30

This local candidate is `746400a690a18856b2e00b1c8dd0242cefefccab` plus PR #70's permission-cache generation fence (`2c42e5d65768307acf3e68369c5a7027553b3bcb`). It is an integrated code checkpoint, not a frozen release candidate. The full candidate diff was applied to a temporary Git index based on `main`, so staged-diff guards evaluated the 183-file integration diff without changing the worktree index.

## Qualification results

- `make verify` — **passed** on this SHA and the exact staged candidate diff. All 16 substrate guards, pre-commit hooks, pinned formatters, backend type/lint checks, frontend lint and type checks, Core wheel import, CI-faithful backend smoke, full backend suite, and frontend suite passed. The frontend suite reported **213 files and 1,274 tests passed**. Default backend skips were the declared Atlas integration, opt-in resolver benchmark, unseeded external App fixtures, and PostgreSQL-only tests covered by the next lane.
- `make test-postgres` — **passed** against the configured local PostgreSQL service. The PostgreSQL-mode suite completed; declared Atlas, benchmark, unseeded App, and end-to-end placeholder skips remain as reported by pytest.
- `make verify-independent-artifacts` — **passed**: Core wheel import, clean-install ASGI import, SDK wheel import, and signed external Asset Register load all succeeded. A separate retained-artifact run built Core and SDK wheels plus a signed Asset Register archive and verified those exact files from a clean environment. The files are retained under `/private/tmp/integral-core-cdfdddd-artifacts-1790754157/`:
  - Core wheel `dist/integral_core-0.1.1rc11-py3-none-any.whl`: SHA-256 `33cecbfbc8d96b6ec114d1d3ba7d4652f74b16810611ec733605985948b4aff9`.
  - SDK wheel `dist/integral_sdk-0.2.0-py3-none-any.whl`: SHA-256 `d73efcb4eff0e6a712a050c583efbceaf5e28ff0d772e9d107bebbbbfcc40175`.
  - Signed external App archive `packages/asset-register-1.0.0.tar.gz`: SHA-256 `c9105e0b2eb38b894c1825c9ff5c440657a18b4512f9f593c900d8bb94dc8676`.
  - Verification public key file `public-key.txt`: SHA-256 `969b247b439f95c8b3ae11790febaef10a8fa58b63d50ef56b6bc01c2c803614`.
  - The clean environment imported the installed Core ASGI app and SDK, passed `uv pip check`, verified the App signature, loaded the external bundle, resolved its `register_asset` handler, and confirmed the workspace tool registration.
- Rebuilding the Core wheel again from the same source produced SHA-256 `37f0a77b682538970e6b2884e2a3a1e42d09f4f146adf9a920a3c6e9134fcd6b`. The two wheel archives contain identical member contents; their ZIP member timestamps differ. The retained wheel above is the exact qualified artifact, while repeatable wheel-byte identity remains an open packaging property.
- Authenticated browser smoke against the candidate API and frontend (`127.0.0.1:14011` and `127.0.0.1:19013`) used the disposable `integral_candidate_746` PostgreSQL fixture. Creating `C6 Candidate cdfdddd Track` showed the new Track immediately; an immediate page reload still showed it. Apps, Feed, Approvals, Background Tasks, Notifications, Settings, and Agent routes loaded. No model prompt was sent.
- Candidate-specific API and web Docker image IDs were not captured. The local Docker Desktop builder did not progress past base-image resolution for either image. The checkout's original Docker contexts included a 568 MB backend virtualenv and 602 MB frontend `node_modules`. PR #94 adds context exclusions and prevents host Node modules from overlaying the clean Linux install. Both Dockerfile builds passed the opted-in GitHub run `36687770270` on PR #94 head `b179d39504efc23a8377d5572e45a122eb75475a`; those runner images were not exported or published and this PR head is not the integrated CDF candidate. No candidate image IDs or registry digests are available.

## What the browser recheck establishes

The preceding candidate `746400a` exposed a short stale-visibility window after Track creation: the Track persisted with its structural graph edges but was absent from the immediate reload and appeared after the permission-cache TTL. PR #70 adds a per-user generation fence so an in-flight pre-invalidation permission read cannot repopulate stale process-cache data. On this integrated SHA, the new Track remained visible after immediate reload. This closes that reproduced browser case for this candidate.

## Remaining C6 and release gates

This is not the complete C6 matrix. The browser smoke does not prove full browser acceptance for every wave, deployed transport parity, current-SHA Docker image identity, or registry image digests. Exact Core/SDK/App archive hashes and the verification public-key identity are retained; byte-for-byte repeatability of the Core wheel build remains open because ZIP timestamps vary across builds. W0.3b external corpus custody/live configuration, W4.5 human usability acceptance, W5.2 migrated-App and human recommendation acceptance, and Product Owner architecture/release review remain open. No release is approved by this record.
