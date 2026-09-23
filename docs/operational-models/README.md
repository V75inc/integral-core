# Operational Models

An **Operational Model** is the living definition of how an Integral App or
Track represents and supports real work. It gives people and agents the same
vocabulary for records, relationships, views, rules, and guidance.

For example, a car-rental Operational Model might define Cars, Customers,
Rentals, service dates, an availability board, a “check out car” operation, and
a resident skill that prepares a renewal reminder. The App Package distributes
that model; an Installed App materializes it in one workspace.

## The vocabulary

| Term | What it is | Example |
| --- | --- | --- |
| Operational Model | The declarative definition of a domain | Car rental operations |
| App Model | A model spanning related tracks | Cars, Customers, and Rentals together |
| Track Model | The model for one record collection | The Cars track |
| App Package | Immutable distributable containing a model and optional code/assets | `car-rental-desk-1.0.0.tar.gz` |
| Model Listing | Discoverable catalog record | “Car Rental Desk” in the model library |
| Installed App | A package applied to one workspace | Acme Rentals’ live Car Rental Desk |
| Model Revision | A draft or published model version | “Add inspection due date” |

## Start here

1. **Describe the work.** Name the records people need to maintain, the
   relationships between them, and the questions they need answered.
2. **Model one Track at a time.** A Track is a typed table. Its entries are
   records. Define entry types, fields, relations, and views.
3. **Create an App Model when several Tracks work together.** Add shared rules,
   cross-track relations, and App-level guidance only when the domain needs
   them.
4. **Add operations for governed actions.** Use a typed operation for a rule
   that must hold regardless of whether a person, the resident, or an MCP
   client invokes it.
5. **Package only when it should be reusable.** An App Package is a delivery
   form for an Operational Model; it is not the model itself.

The example-led [App authoring guide](../developer/quickstart.md) walks through
this with a Studio Equipment Desk. The same progression works for car rentals,
client services, project delivery, or any other operational domain.

## Contract surface

The Operational Model contract is consistent through its persistence nodes,
REST surface, package manifest, resident tools, and user routes. There is no
legacy compatibility namespace.

- [Operational Model decision](../backend/adr/013-operational-model-vocabulary.md)
- [App extension contract](../platform/extension-contract-v1.md)
- [App bundle reference](../backend/app-bundles-v1.md)
- [Agent contract](AGENT_CONTRACT.md)
- [Model draft, publish, and migration lifecycle](DRAFT_PUBLISH.md)
