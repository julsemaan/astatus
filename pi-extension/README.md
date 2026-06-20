# pi-extension

Pi extension package for `agent-status/v1alpha1`.

Structure follows `ponytail` pattern:
- repo root `package.json` exports pi resources
- `pi-extension/index.js` holds extension entrypoint
- `pi-extension/package.json` holds dev-local test script

## Install

From repo root:

```bash
pi install /path/to/astatus
# or
pi install git:github.com/you/astatus
```

Pi loads `./pi-extension/index.js` via root `package.json` `pi.extensions` manifest.
