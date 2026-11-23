# Optional local model for a private Web Chat test

`build_private_lm_studio_text_model(model_id)` is an opt-in model adapter for
reviewed local hosts. It does not register a route, configure or start LM
Studio, discover models, touch provider accounts, or alter Core's general
outbound URL policy. Do not plug it into a public host or a staff chat
resolver. A private preview host owns caller authorization, profile identity
and readiness checks, rate limits, and whether it installs the adapter at all.

An operator must first approve the local service, confirm it is bound only to
`127.0.0.1:1234`, and pin an exact, already loaded, chat-capable model ID.
Listing available models does not prove a model is loaded or can return chat
text. Review LM Studio's auto-loading behavior, memory and capacity, and
prompt logging/retention independently. Use only synthetic, non-sensitive test
messages and a synthetic public persona until those controls are reviewed. A
shared local instance can still compete with other work for memory or GPU.

The adapter sends only the selected persona and a single text test message
under fixed tool-free rules. It uses no user, workspace, staff context or
credential, and never retries, redirects, asks for another model, or handles
tool responses. If the reviewed service requires a credential or cannot supply
that model on the pinned route, leave this adapter unavailable. The endpoint
is a fixed application-side loopback boundary, never a URL field from a
connector's browser form. A model failure returns one generic error; it does
not certify that Web Chat is published or visitor transport is available.
