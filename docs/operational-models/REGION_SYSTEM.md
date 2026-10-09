# Reusable regions

Regions compose focused portions of an operational interface: forms, related collections, summaries, actions, charts, trees, and overlay containers where registered. They share the palette's contract and the surrounding App's authority.

Choose a region from its manifest and configure only supported inputs. A form needs a declared record type and field configuration. A relation region needs a valid authorized relationship. A chart needs a bounded data source with an explicit shape.

Overlay containers such as modal, drawer, and popover regions organize interaction. Their placement does not create another permission model. Loading, empty, denied, error, and unavailable states must remain understandable.

Creation flows validate required fields and schema through canonical writers. A multi-step wizard does not authorize a partial record merely because the first screen completed. Test cancellation, invalid input, revision changes, successful save, and browser readback.

Use [view manifests](VIEW_PALETTE.md), [component guidance](UI_COMPONENTS_GUIDE.md), and [extension presentation](../platform/extension-contract-v1.md#presentation) rather than copying obsolete domain-specific UI recipes.
