# BSV receiving QR card

The self-contained `bsv-receive-qr-card.bundle.js` is a Home Assistant
Lovelace module. Register it as a module resource, then add:

```yaml
type: custom:bsv-receive-qr-card
entity: sensor.bsv_operator_wallet_mainnet_operator_wallet_status
grid_options:
  columns: 12
  rows: auto
```

The card reads `receive_address` and `network` from the configured wallet status
entity. It generates the QR locally, sends no address to a third party and
does not sign or broadcast transactions. The payload is the bare address,
not a Bitcoin URI, public identity key or payment request. Check the address
in a BSV wallet before sending. Back up the operator wallet before funding.

Unavailable entities, other networks and malformed addresses suppress the QR.
Address updates replace the QR. The card checks address shape, not the
Base58Check checksum; the integration supplies the SDK-generated address.

## Rebuilding

Install `qrcode-generator@2.0.4` and `esbuild@0.25.12` in a temporary build
directory. With that directory's `node_modules` on `NODE_PATH`, run:

```sh
esbuild frontend/bsv-receive-qr-card.js --bundle --format=esm --minify \
  --banner:js="/*! $(cat frontend/qrcode-generator-LICENSE) */" \
  --outfile=frontend/bsv-receive-qr-card.bundle.js
```

The bundled QR generator is MIT-licensed. See `qrcode-generator-LICENSE`.
Inline HA resource registration is supported because the bundle has no
external imports. Existing dashboard resources and cards need not change.

## Validation

On 2 October 2026, Chromium tests confirmed that the bundled card:

- Encodes the exact example address, independently decoded with ZXing.
- Regenerates the QR when the receiving address changes.
- Hides the QR for unavailable entities, testnet and malformed addresses.
- Makes no network requests while rendering.
- Fits a 375-pixel mobile viewport and remains readable in a dark theme.

The live dashboard configuration was read back after deployment and matched
the previous configuration with only this card added. Live HA screenshot
verification was unavailable. Refresh the dashboard frontend to load a newly
registered resource; no HA restart is needed.

## Session payment review card

`bsv-session-review-card.js` is the source for the separate review workflow.
Build it with the same pinned QR generator and esbuild versions, preserving
the MIT banner, and copy the bundle to
`custom_components/bsv_settlement/frontend/session-review-card.js`.

The integration serves the installed bundle at
`/bsv_settlement/session-review-card.js`. Register that URL as a dashboard
module resource after activating the updated integration. See the
[session payment review guide](../docs/session-payment-review.md) for card
configuration, permissions, approval boundaries and limitations.
