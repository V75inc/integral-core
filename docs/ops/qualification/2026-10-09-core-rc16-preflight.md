# Core 0.1.1rc16 release preflight

Date: 2026-10-09 (America/Guyana). Qualified code revision: `bfe0a3dd1cb61dd838ed081997e04cbdd0097667`, including PR #124 and the subsequent native configuration cleanup. This record qualifies the managed local Core runtime and prepares PR #116 for a TestPyPI release candidate. Publication, the release tag, and registry readback remain separate gates after successful CI on the exact main revision.

## Findings resolved

- **Release source attribution:** both publication workflows referenced the source qualification job from their tag-recording jobs without declaring it as a direct dependency. The source SHA would be unavailable to those jobs. Both workflows now declare that dependency. A smoke regression test reproduced both failures and passes after repair. See [GitHub's needs context contract](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#needs-context).
- **Model setup:** the outer AI Models description still advertised the removed model slots. It now describes the primary chat model and optional voice input, matching the controls and native route.
- **Release instructions:** the guide now describes successful exact-revision CI and manual workflow dispatch as the publishing triggers. A tag push alone does not publish.

These followups change release tooling, regression coverage, documentation and one settings description. Backend runtime source is unchanged from `a9db035f2ee66dbe8408a212fd95b6dfdc56fb9d`.

## Acceptance evidence

| Gate | Result |
| --- | --- |
| Frozen development and test dependency sync | Passed on Python 3.11.15 |
| Entire staged PR / substrate boundary guards | All 16 passed |
| Pinned formatting, pre-commit, TypeScript, frontend lint | Passed; existing lint warnings remain non-blocking |
| `make verify` | Passed; backend 4,959 passed / 286 skipped / zero failures or errors; frontend 279 files / 1,583 tests passed |
| `make verify-pr` | Passed, including Core-only, reference extension contracts, environment-isolated smoke, frontend, PostgreSQL spikes and contracts |
| Full isolated `make test-postgres` | Passed; 14 reported skips; disposable PostgreSQL 16 with pgvector available on loopback port 15436 |
| `make verify-independent-artifacts` | Passed Core wheel, fresh public dependency resolution, SDK wheel and external reference App proofs |
| `make audit` | Passed the existing advisory policy; accepted exceptions remain |
| Wheel and source distribution | Built; `twine check` passed; no dotenv files, domain packages or private qualification traces |
| Installed distribution | Fresh Python 3.11 environment, public PyPI dependencies, `pip check`, blank and explicit-App initialization, packaged web startup passed |
| Packaged browser / real authentication | Sign-in, generic App creation and API readback passed; anonymous substrate access returned 401 |
| Native read-only lookup | Correct App list, persisted transcript and API agreement; three model steps; 15,504 input / 163 output tokens |
| Backup / restore | Managed PostgreSQL App and completed native conversation restored into a fresh installation; existing authentication remained valid; browser reopened the stored answer |

The installed, updated candidate wheel is `integral_core-0.1.1rc16-py3-none-any.whl`, SHA-256 `0c000c0efe9bbf8347b6f6b2b4830ce7fcf43fb98057c6ee63ecea23d8b6fe1d`. It contains the built workspace and all 16 Core skills. Its reproducible ZIP timestamp came from revision `a9db035f`; the registry build will use the actual main release revision and must have its own digest readback.

The broad gate ran in an isolated checkout without a developer `.env`. An earlier invocation incorrectly forced `INTEGRAL_ENV_FILE=/dev/null` for every test, disabling the dotenv behavior that one test intentionally exercises. The normal invocation passed the complete suite. Only the CI reproduction uses explicit environment-file isolation. Neither assertions nor gates were weakened.

## Qualification boundaries

The live smoke uses the configured Ollama Cloud model through the native harness and managed direct chat. It verifies a generic lookup and completed-turn persistence; it does not certify every provider, BYOK combination, unknown external effect, partial-batch outcome or hosted durable-chat deployment. Provider cost is not certified by this smoke and must not be treated as zero.

Atlas integration lacked `ATLAS_TEST_URI`. Selected vector integration tests reported an unavailable endpoint/extension; the presence of pgvector in the test image does not establish vector qualification. Slow and domain App suites remain outside these Core gates. The packaged CLI backup/restore drill supplies separate managed PostgreSQL evidence.

Core remains domain-neutral. No commercial App plans or packages are added. Existing facet migration, agentive walker work and non-runnable work-mandate foundations retain their documented status. Read the [foundation assessment](2026-10-09-core-foundation.md) and [capability boundaries](../QUALIFICATION.md) before stable or hosted promotion.
