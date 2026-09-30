# Local Ollama provider smoke

**Date:** 2026-09-29

**Candidate:** `3da6199ff4aa1c0405562f5d9f9d348e7b504786`
**Purpose:** Check that the local Ollama endpoint and the candidate's LiteLLM model route can complete a small, domain-neutral request without a hosted provider credential.

## Configuration

- Ollama endpoint: `http://127.0.0.1:11434`
- Local model: `gemma4:e2b`
- Ollama model digest: `7fbdbf8f5e45a75bb122155ed546e765b4d9c53a1285f62fd9f506baa1c5a47e`
- LiteLLM route: `ollama/gemma4:e2b`
- Credential mode: local endpoint, no API key supplied
- Candidate Python runtime: repository backend virtual environment

## Results

| Path | Result | Input tokens | Output tokens | Elapsed |
| --- | --- | ---: | ---: | ---: |
| Ollama `/api/chat` JSON response | Passed; returned the requested JSON | 32 | 101 | 7.53 s |
| LiteLLM completion using `ollama/gemma4:e2b` | Passed; returned `READY` | 27 | 2 | Not captured |

The Ollama response identified `gemma4:e2b`; LiteLLM reported the routed model as `ollama/gemma4:e2b`. No OpenAI endpoint, hosted Ollama model, credential, domain prompt, or held-out corpus was used.

## Qualification boundary

This smoke establishes only local provider and LiteLLM route compatibility. It did not invoke Integral's resident harness, exercise an authoring scenario, or measure a full journey. It is not a W0.3b pass and does not satisfy the live qualification profile. The split manifest remains `pending_q_custody`; Q must still record the immutable corpus locator/version/digests and approve the scoped run configuration and budgets before the held-out run can start.
