# Reference native SDK Home

This optional external App demonstrates a Home rendered directly by Integral:
responsive native layout, metric, chart, records, a form and a typed Python App
action. It needs no iframe, HTML bundle or Core registry edit.

`operational-model.yaml` supplies the packaged native default. `ui/home.mjs`
shows reusable composition functions using `@integral/sdk-ui`; serialize their
result into `app.home` or use the public Home preview/update API. Business logic
uses the existing public tool facade. The example action echoes a message.

Build an external directory bundle:

```sh
python3 examples/reference-sdk-home/build.py --output /tmp/reference-sdk-home.zip
```

Extract under an external package root, configure `INTEGRAL_PACKAGE_PATHS`, then
install Reference SDK Home through Manage Apps. Generic profile import validates
configuration; executable bundle discovery preserves the Python tool source.

Installed customizations persist separately from package defaults. Read the
Home revision, preview a change, then save against that revision. The resident
uses the same contract and ordinary approval. Stable component IDs keep drafts
intact during updates. `{home:{}, expected_home_revision:"..."}` restores defaults.
