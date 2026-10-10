# Agent authoring contract

An agent can help describe and revise an Operational Model using exposed authoring capabilities. Its output is a candidate artifact, not authority to publish or execute it.

Discover current field types, view types, relation targets, and authoring contracts through substrate introspection. Preserve existing introspection keys and shapes when extending the interface. Supply explicit authorized workspace and target identity; do not derive permission from an ID in generated text.

The template authoring service is deterministic catalog matching and structured fill. The resident's model can perform the reasoning before calling it; the service is not itself a hidden general LLM generation call. Type-hint ambiguity and explicit picker conflicts must be handled honestly.

Apply modifications through the declared patch allowlist and draft/publish lifecycle. Validate fields, relations, views, and impacts before proposing publication. Supported migrations handle old records; forcing publication is a separate destructive authority.

Skills provide guidance, while Core supplies validation, policy, staging, and receipts. A design-only request must remain a design until implementation is intended. See the [resident delivery flow](../product/RESIDENT_DELIVERY_FLOW.md).
