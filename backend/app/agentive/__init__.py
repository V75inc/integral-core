"""Agentive layer for Integral.

Always-on ops layer on a pluggable harness (default: Integral AI/Pydantic AI;
Integral AI is the only resident harness). Hosts
the singular resident mind per active harness binding (facets: personal /
org-facing / system), the MCP tool surface for external agents, staging,
skills overlay, and conversational context.

``AGENTIVE_ENABLED`` (if set) remains an operational kill-switch for
deployments that must run substrate-only — it is not a design ceiling.
Features are designed harness-first; see docs/product/RESIDENT_HARNESS.md and
docs/backend/adr/003-singular-resident-harness.md.
"""
