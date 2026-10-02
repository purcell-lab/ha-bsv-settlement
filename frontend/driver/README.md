# Driver page build

This standalone page is served by the HA custom component. It is not a separate
backend and requires no driver-side HA credentials.

```sh
npm ci
npm test
npm run build
cp index.html style.css ../../custom_components/bsv_settlement/frontend/driver/
cp node_modules/@bsv/sdk/LICENSE.txt ../../custom_components/bsv_settlement/frontend/driver/BSV-SDK-LICENSE.txt
```

See [the operator and driver guide](../../docs/driver-session-budget.md).
`model.test.js` uses fictional keys; `cross-sdk.cjs` is a test-only helper for
Python/TypeScript signature interoperability. Neither ships in the public page.
