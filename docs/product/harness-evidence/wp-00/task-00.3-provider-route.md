# WP-00.3 — LiteLLM route and usage boundary

## Finding

Integral's deployed chat credentials are user-owned and are resolved by workspace owner. `model_credential_resolver.py` produces a LiteLLM route model ID and decrypted provider key, and jvagent's runtime currently applies that override to its LiteLLM model action. The backend pins LiteLLM 1.101.x. No LiteLLM proxy URL or gateway route is configured in the source/configuration surfaces inspected.

Pydantic AI 2.54.0 includes a documented `LiteLLMProvider`, but it sends OpenAI Chat Completions requests over HTTP to `api_base`. Integral's existing route is the in-process LiteLLM Python SDK, with per-workspace key routing, rather than an HTTP LiteLLM proxy. A stock `LiteLLMProvider` would therefore not preserve the present route as-is. A direct provider SDK call from the Pydantic agent would bypass the existing LiteLLM routing, cost attribution and callback path and is rejected for the native runtime.

## Candidate integration boundary

WP-04 should supply a Core-owned Pydantic AI model adapter backed by LiteLLM's in-process async completion API, with an immutable per-turn route/credential profile from the existing resolver. Before each physical SDK dispatch, the adapter must call Core admission and persist a unique model-request intent. LiteLLM request/response callbacks or the adapter response must capture provider, route, request ID, status, timestamp, usage token categories, cached/reasoning tokens, provider-reported cost when supplied, and missing/estimated-price state. Every LiteLLM-internal retry/fallback needs either a unique physical-attempt callback correlated to the logical request or must be disabled and reimplemented through the Core adapter so accounting does not treat multiple upstream requests as one. Stale/uncertain outcomes remain unsettled until reconciliation. These are design requirements, not implemented behavior.

The first adapter prototype should use a mocked `litellm.acompletion` transport and TestModel side-by-side, establishing conversion for text, tools and usage without a provider call. Streaming, multimodal content, exception/unknown outcome, retries and returned cost require separate fixtures. Do not select the Pydantic `LiteLLMProvider` simply because its name matches the SDK.

## External route status

No provider API key, paid request, live model reply, LiteLLM callback event, response usage or provider cost was tested in this package. No key value was inspected or recorded. The plan's live route claim remains unqualified until a separately budgeted one-request probe has a model/credential and cost ceiling. Offline TestModel `RunUsage` is synthetic framework usage and is not a billable receipt.

## Dependencies and tests already qualified

The exact-pinned Harness package is in Core dependencies and lock at 0.30.0 (Pydantic AI Slim 2.54.0). Frozen sync and `uv pip check` succeeded. Two repository contract tests pass with no external calls: `backend/tests/native_harness/wp_00/test_composition.py` and `backend/tests/native_harness/wp_00/test_deferred_approval.py`. These tests prove upstream capability composition and deferred-call resume, not the proposed Core LiteLLM adapter.

## Next implementation action

In WP-01, define the model request envelope and event/usage contracts against Integral's intelligence plane; in WP-04/05, implement the adapter and append-only physical request observations. Define exact supported Pydantic tool and multimodal part sets. Unknown parts must fail closed with a typed error until covered, rather than silently stringify user content.
