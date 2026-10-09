# Skills: format, discovery, and authority

Integral uses the [Agent Skills specification](https://agentskills.io/specification). A skill is a directory containing SKILL.md with YAML frontmatter and a Markdown body. Required frontmatter is `name` and `description`; optional standard fields carry compatibility, license, metadata, and allowed-tool hints.

Names use lowercase letters, numbers, and hyphens, match the directory, and stay within 64 characters. Descriptions are non-empty and at most 1024 characters. Vendor fields, underscore disk names, and list-form allowed-tools are not accepted by Core's standard-format guard.

The body has no mandatory headings. Write useful instructions, grounding requirements, procedures, and examples without turning style preferences into format requirements. Descriptions support discovery; full instructions and authorized resources load progressively.

App manifests must match on-disk descriptions. Runtime overlay names use `{app_slug}__{skill_key}`; disk names and operation/tool identifiers are separate. Only active accessible Apps in the current workspace contribute skills.

A skill supplies instructions, never access, policy grants, or approval. Validate with the compliance guard and relevant skill tests. Changes to core skills or example Apps also require capability-map regeneration.
