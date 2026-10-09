# Add useful interface complements

Begin with the work a person needs to understand. A request might need a summary, an evidence collection, a review state, and an action to prepare a follow-up. These are generic examples; a domain App supplies their actual semantics.

Use registered regions and declared queries/operations to assemble the interface. Preserve base/custom-field namespaces, relation identity, current permissions, and unavailable states. Package-owned presentation should travel with its contract rather than requiring Core to recognize the App's name.

Give actions meaningful labels and visible outcomes. Preparing a chat draft is separate from sending it. Review controls must show the actual scope and status rather than suggesting a proposal has already applied.

For sensitive information, change the resource model. A hidden region or field is not privacy. See [composition](COMPOSITION_PATTERNS.md), [UI components](UI_COMPONENTS_GUIDE.md), and [packs](UI_PACKS.md).
