# WP-03.3.3 — Native browser smoke evidence

**Date:** 2026-10-05
**Branch:** `feat/pydantic-ai-harness-v1`
**Environment:** isolated Integral Core development stack; frontend `127.0.0.1:9011`, backend `127.0.0.1:4011`, dedicated PostgreSQL database `integral_v1_branch`
**Harness:** Integral AI / Pydantic AI Harness
**Model:** `gpt-4.1-mini` (OpenAI control)

## Scenarios

### Scaffold design, confirmation boundary

In the browser, requested a simple Guyana maintenance-company equipment register with serial number, description, condition, storage location, purchase date, photo, staff checkout/return dates, and the Integral scaffold skill. The prompt explicitly said design only and to wait for confirmation before building.

The assistant returned a proposed Equipment Register app with one Tools track, all requested fields, Table and Calendar views, and no demo entries. It explicitly said that nothing had been built and asked for confirmation or changes. No build confirmation was sent and no App was created.

The response displayed model, aggregate token spend, cost, elapsed time, and provider-call count.

### Workspace and tracks lookup

In a separate browser conversation, asked: “What workspace is this, and are there any tracks in it? Please check the workspace rather than guessing.” The trace showed successful calls to `integral_get_scope` and `integral_list_tracks`. The assistant correctly identified `Harness V1 Smoke` and reported no tracks.

The response displayed `gpt-4.1-mini`, 10.1k tokens, `$0.0022`, 4.0 seconds, and two provider calls.

Reloading the browser restored both conversations and the complete lookup response, including its tool-step trace and usage readout.

## Result and boundary

Both user-visible smoke scenarios passed on the isolated branch stack. The tests exercised the existing native `/messages` streaming path and its graph-backed chat transcript. The scaffold confirmation boundary held: the proposal remained unbuilt.

The PostgreSQL worker suite also simulated worker death at both terminal boundaries: after the stable assistant transcript was persisted but before WorkItem terminalization, and after the final event was committed but before transcript persistence. Retrying the same claimed turn reconciled the committed result and terminalized without calling the provider a second time in either case.

These scenarios do **not** exercise the WP-03.3.3 durable WorkItem producer, committed event replay endpoint, WorkItem cancellation, or multi-process recovery; those paths remain gated and unaccepted. Browser reload here proves transcript persistence only, not WorkItem replay or that a model request was not rerun after a transport interruption.
