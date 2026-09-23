# Integral SDK

`integral_sdk` is the public, dependency-light Python contract for Integral
App authors. Core injects the runtime context; an App imports these types and
must not import `app.models`, `app.services`, or jvspatial persistence internals.

The supported contract is documented in
[`docs/platform/extension-contract-v1.md`](../../docs/platform/extension-contract-v1.md).
