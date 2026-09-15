"""Speech-to-text for the agent chat (voice input) and the transcription tool.

- ``base`` — the provider-neutral adapter contract.
- ``providers/`` — one module per vendor; pure HTTP, no graph or crypto.
- ``registry`` — adapters keyed by provider slug.
- ``service`` — the only caller that joins the workspace role gate, the
  credential resolver, rate limits and an adapter.

Adding a provider: write an adapter in ``providers/``, list it in
``providers.builtin_providers``, add its slug to ``SpeechProvider`` in
``app/schemas/model_credentials.py``, add its browser origins to every CSP
source, and register a frontend engine for its ``client_engine``.
"""
