# Integral SDK

`integral_sdk` is the public, dependency-light Python contract for Integral
App authors. Core injects the runtime context; an App imports these types and
must not import `app.models`, `app.services`, or jvspatial persistence internals.

The supported contract is documented in
[`docs/platform/extension-contract-v1.md`](../../docs/platform/extension-contract-v1.md).

## Declared aggregate queries

`AggregateSpec` and `AggregateResult` are the typed shapes for an App's
declared aggregate query. The App declares the query in its manifest with
an input/output schema, applies its own domain authorization, and returns an
exact `AggregateResult` from its handler. Core's `integral_aggregate` uses the
same count/sum/avg/min/max/distinct semantics for open-class Tracks. It does
not scan packaged App records. A failed or over-budget App query must return
an error rather than a partial `AggregateResult`.

```python
from integral_sdk import AggregateResult, AggregateSpec

spec: AggregateSpec = {"op": "sum", "field": "value", "group_by": "stage"}
# The declared App handler consumes spec and returns AggregateResult.
```
