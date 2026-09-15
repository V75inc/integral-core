"""Agentive layer for Integral.

Always-on ops layer on a pluggable harness (default: embedded jvagent). Hosts
the singular resident mind per active harness binding (facets: personal /
org-facing / system), the MCP tool surface for external agents, staging,
skills overlay, and conversational context.

``AGENTIVE_ENABLED`` (if set) remains an operational kill-switch for
deployments that must run substrate-only — it is not a design ceiling.
Features are designed harness-first; see docs/product/RESIDENT_HARNESS.md and
docs/reviews/2026-09-harness-full-sweep.md.
"""
