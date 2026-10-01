# C6 candidate qualification — 2026-09-28

## Candidate

- **Source revision:** `c13db8099109a71ac1c5b3a87aa85c6bee5c43ab`
- **Scope:** fixes first-created Track visibility by invalidating the caller's accessible-Track cache after creation; includes the frontend race fix and regression coverage already present at this revision.
- **Core wheel:** `integral_core-0.1.1rc11-py3-none-any.whl`, SHA-256 `b9459f8db6914d9316261c57cd71fe3d1cce2b9bb3ba345787e6d09a779ecdd3`.
- **SDK wheel:** `integral_sdk-0.2.0-py3-none-any.whl`, SHA-256 `4cd8b631e2e61bdc07950be03022111c91dce06ed7d669f100e8c15961268cad`.
- **Independent App:** `asset-register-1.0.0.tar.gz`, SHA-256 `7c69f6c6f402b671ac10f994fd458adf025eccca391c5ac2d19abdd6be19fce6`; public signing-key fingerprint (SHA-256 of the public-key file) `dbb5b894e6a3cc1303fd413f011fca4023a6143ade173f9623469e941480ad75`.

## SHA-bound qualification lanes

All records below identify the same source revision and non-secret configuration digest `c0666a2ef611d9bcb8073281fc4a9c4d26d012b712cdef39b076667cffa78658`. Their detailed output is in local, ignored `.qualification-evidence/` logs; the summary here is the durable repository record.

| Lane | Result | UTC interval | Local evidence |
| --- | --- | --- | --- |
| Repository (`make verify`) | Pass | 11:32:36–11:42:04 | `.qualification-evidence/2026-09-28T11-32-36.302515+00-00-repository.log` |
| Core-only (`make verify-core-only`) | Pass | 11:42:14–11:42:24 | `.qualification-evidence/2026-09-28T11-42-14.976702+00-00-core-only.log` |
| Contract (`make verify-contract`) | Pass | 11:42:14–11:42:59 | `.qualification-evidence/2026-09-28T11-42-14.976753+00-00-contract.log` |
| Independent Core, SDK, and App artifacts (`make verify-independent-artifacts`) | Pass | 11:42:14–11:42:43 | `.qualification-evidence/2026-09-28T11-42-14.976781+00-00-artifacts.log` |
| Postgres suite (`make test-postgres`) | Pass | 11:43:05–11:50:30 | `.qualification-evidence/2026-09-28T11-43-05.028741+00-00-postgres.log` |

The repository lane includes the staged-format/pre-commit checks, type checks, CI-faithful smoke lane, frontend suite (209 files / 1,257 tests), and full backend suite. Optional Atlas and slow benchmark tests were skipped as declared by their fixtures. The Postgres lane used two xdist workers with separate test databases. Its restore rehearsal created and removed a scratch database and asserted matching graph counts and identity; it did not restore over the source database.

The independent artifact lane reported `artifact-wheel-import-ok`, `clean-install-asgi-import-ok`, `sdk-wheel-import-ok`, and `external-asset-register-load-ok`. The workflow created an ephemeral signing key for the lane; no private key is retained here.

## Browser acceptance

Browser acceptance ran against the isolated Compose project `integral-core-c6`, with API, web, and Postgres on host ports `14000`, `19006`, and `15433`. The candidate API image was `sha256:a234409830b27ed73a32b0cdbc7c34aef231fbf0ce811c0529ae4375e1f42ca3`; web image was `sha256:9dda027a67f56ffd91b011f01c577f432e6e331261af86730826f1c7e8348d4a`; Postgres image was `sha256:fa3d9bb7ee77f5c1f0bfb009a9df30243c040896825f3033b09a77101bb2ca95`. The frontend source did not change between the previously built web image and this candidate revision. The API was rebuilt from the candidate source. The database used the project-specific `integral-core-c6_integral_pg` volume; no other database volume was used.

The candidate ran with model-provider credentials absent and a disposable OAuth encryption key. API health reported healthy with the database connected. Recent API access-log status codes were all `200` during the recorded smoke window.

On a newly created synthetic account in the isolated candidate database, the Tracks page began with zero tracks. Creating the first default Track made it appear in the list immediately without a manual refresh. A full page reload then returned the Track in the list. This exercised the formerly stale empty accessible-Track cache and the post-create invalidation on the candidate API. `/apps`, `/tracks`, `/feed`, `/approvals`, `/background-tasks`, `/notifications`, `/settings`, and `/agent` loaded in the same UI build with their expected empty states.

## Evidence boundaries and status

- Candidate-specific automated qualification and browser acceptance are recorded as passing.
- Transport parity remains **partial**: the candidate browser covered ordinary account, Track, and navigation flows; extracted-App HTTP, resident, and MCP paths are covered by contract tests, not by this browser session.
- No model-provider dialogue, mobile/responsive audit, axe accessibility audit, or screenshot-baseline comparison was run.
- The Product Owner's architecture and release review is **pending**. This evidence does not approve publication or declare C6 passed.
