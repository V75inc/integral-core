# Integral Core foundation reset

**Date:** 2026-09-20
**Status:** Implementation plan; target direction accepted by the product owner, detailed contracts proposed.
**Source baseline:** `75a0f35c2d4308b268fda0d8b15775ce9fbcacae`. Untracked assessments are evidence inputs, not release certification.

Integral Core will become a modular monolith for an intelligent operational support environment: a shared, governed information and application runtime used by people, the resident agent and external clients.

This package plans the adaptation of the existing project and replacement of its documentation. It does not certify the runtime, modify data, supersede current agent instructions, or perform the documentation deletion itself.

Read in order:

1. [Architecture decision and target contracts](architecture.md)
2. [Implementation program and acceptance gates](implementation-plan.md)
3. [Documentation replacement plan](documentation-plan.md)
4. [Complete baseline Markdown disposition register](documentation-inventory.csv)

The inventory assigns every tracked Markdown document and the current untracked assessment documents a proposed disposition. It includes root guides, nested agent instructions, runtime skills and hidden planning history. Dependency/vendor trees and local smoke output are outside the authored documentation baseline. Non-Markdown documentation and embedded instructions are explicitly inventoried in WP-00 before deletion.

## Success criterion

A new operator installs a release artifact, describes an operational need, reviews a validated proposal and receives a usable application. People and agents can query, operate and evolve it through the same governed contracts. Interruptions produce accurate, recoverable state. An independent developer extends it without importing private Core internals.

No task is complete solely because code exists, a model says it succeeded, or a happy-path unit test passes. Each package below requires executable acceptance evidence and accurate documentation.
