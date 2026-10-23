# Chat controls and cloud routing review — 2026-10-23

## Changes

- A short error with identical details has no disclosure caret. Clipped summaries expand in place; distinct details mount only while expanded.
- Composer attachment, suggestions, model/reasoning, microphone and send controls use the shared hover/focus tooltip pattern.
- The brain button opens a compact settings panel: one grouped model selector, a Light/Heavy readout, and a collapsed Reasoning section showing the current level. Reasoning levels use labeled selectors rather than a long action menu. Idle owned conversations can override supported reasoning levels by model role, or return to profile defaults. Model changes clear overrides; stale unsupported preferences fall back to the current profile. Shared profiles and run ceilings are unchanged.
- Typed Pydantic AI output-retry failures receive an actionable, content-free error. Native provider failures and usage-limit errors retain priority. Capability discovery is still enforced.
- Known Ollama DeepSeek V4.1 Flash and GPT-OSS 120B tool contracts override incomplete gateway metadata. Unknown models explicitly marked tool-incompatible remain rejected for Automatic routing.

## Live isolated preview

Preview: `http://127.0.0.1:9155`, API port 4473; the personal installation was untouched.

The user supplied cloud providers through Settings. Configured Automatic routing with GPT-4.1 (OpenAI) as Light and DeepSeek V4.1 Flash (Ollama Cloud) as Heavy. GPT-OSS remains available as another saved profile. No credentials were copied into this report.

| Check | Observed result |
| --- | --- |
| Save and read back routing | Automatic; Light GPT-4.1; Heavy DeepSeek; saved Heavy reasoning High |
| Fresh greeting | GPT-4.1 completed, 4.4 seconds, 1.4K reported tokens |
| Quick reasoning override | Heavy Low selected; shared profile stayed High; reset to Model default succeeded with toast |
| Read-only workspace App request | Light escalated to Heavy; two tools; three linked Apps; 11.6 seconds, 38.6K aggregate reported tokens |
| Expanded activity | Both model roles listed; `integral_list_apps` showed “Returned 3 results” and 0.5 seconds |
| Original failed conversation | Switched from Heavy only to Automatic; next response completed on DeepSeek in 14.6 seconds |
| Original brief error | One message, no duplicate disclosure caret |

These are individual functional smoke observations, not latency benchmarks. The App request recorded first tool requested at 4.7 seconds and first operational tool started at 6.4 seconds. Cost was reported for only one of five model calls; missing provider costs are displayed as unavailable.

Earlier “Heavy smoke response” messages came from synthetic local profiles that did not honor initial discovery retries. The discovery guard was not weakened. Real cloud tests above replace that fixture as evidence of provider interoperability.

Tool support references: [DeepSeek](https://ollama.com/library/deepseek-v4.1-flash), [GPT-OSS](https://ollama.com/library/gpt-oss:120b-cloud), plus local Ollama `/api/show` capability metadata for those cloud aliases.

## Invariants preserved

I-GRAPH-01/02: one scalar preference map on the existing rooted ChatThread, no new graph records. Workspace and principal checks remain server-authoritative. Field-only compare-and-set prevents preference writes during an admitted turn. Both route construction and physical dispatch validate the current plan; owner funding, policy, tool discovery, effect boundaries, and shared budgets remain binding. No private reasoning or raw provider exception text is added to public activity.

## Automated validation

Passed the CI-faithful smoke lane; full default backend selection with `PYTEST_ADDOPTS='-n auto --dist loadfile' make test-backend`; targeted PostgreSQL AI configuration/routing tests; pinned formatters, backend type checks, and staged substrate guards/hooks. The serial full-backend attempt was stopped and replaced by the full parallel run; no selection was omitted. The final chat panel passed all 2,156 frontend tests and the TypeScript/production build. Browser review confirmed the compact selector, Light/Heavy readout, and expandable reasoning selector.
