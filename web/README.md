# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

## Environment variables

Vite only exposes variables prefixed with `VITE_` to the browser bundle. This project has two:

| Variable | Needed for | Notes |
| --- | --- | --- |
| `VITE_VAPID_PUBLIC_KEY` | Web Push subscription (docs/04-api.md, "Alertas y notificaciones") | The public half of the server's VAPID key pair, base64url. Public by design, so it ships in the bundle; it must match the server's configured key pair. Without it the app runs normally but push stays off. |
| `VITE_API_TEST_BASE_URL` | Vitest only | Never set outside tests; `vite.config.ts`'s `test.env` sets it. |

`API_PROXY_TARGET` is deliberately **not** `VITE_`-prefixed: it is this dev proxy's forwarding target and must never reach the browser bundle.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend enabling type-aware lint rules by installing `oxlint-tsgolint` and editing `.oxlintrc.json`:

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "typescript", "oxc"],
  "options": {
    "typeAware": true
  },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```

See the [Oxlint rules documentation](https://oxc.rs/docs/guide/usage/linter/rules) for the full list of rules and categories.
