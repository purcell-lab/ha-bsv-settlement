# Automatic confirmed-credit receipt sync

This frontend-only change follows PRs #54 and #55. Operator credits can already be sent while the driver is offline; receipt sync imports those existing payments into the registered wallet and records its signed acknowledgement.

## Driver experience

- Opening a private page inside BSV Browser syncs confirmed, unacknowledged credits when an injected wallet is detected. An explicitly paired wallet or an existing collection connection can also be used.
- Late wallet injection and newly confirmed credits are checked while the page is visible. Ordinary browsers are not automatically probed for localhost wallet services.
- Wallet permission prompts can still appear. A declined prompt, identity mismatch, import error or report error pauses automatic retries for the page lifetime.
- **Sync confirmed credits to wallet** provides an explicit retry and ordinary-browser wallet discovery fallback.
- A successful import followed by reporting failure retains the existing in-page import/signature cache. Retry reports the same receipt without another import in that page.
- Reloading uses server acknowledgements to skip completed receipts. An import whose acknowledgement was not saved may need the same existing receipt imported again after reload; no replacement payment is made.
- Expired spending approval does not become renewed authority. Historical receipt access still uses the original private capability and its existing server guards.

## Safety boundary

Automatic sync has its own connection path. It does not assign the wallet to the `connectedWallet` variable used to enable debit collection, clear a collection hold, register a new receiving destination, sign spending consent, create a transaction or broadcast funds.

Only provider-confirmed, unacknowledged rows are considered. Identity, transaction, session, recipient, remittance and proof checks remain in the existing receipt importer. Automatic and manual sync runs are serialized; a failed batch stops rather than generating repeated permission prompts.

The server's acknowledgement remains a signed wallet report, not an independent audit of its spendable balance. This feature cannot deliver receipts into a closed wallet; it syncs when the private page is opened.

## QA inventory

- Injected wallet: direct and ongoing confirmed receipts sync without a button click.
- Plain browser: no automatic transport probe; manual sync remains visible.
- Wrong wallet and declined import: stop before further imports; no polling prompt loop.
- Reporting interruption: distinguish imported/report pending; retry same receipt.
- Concurrent click/poll: one serialized batch; successful receipts skipped.
- Already acknowledged, unconfirmed, hidden and framed pages: no import.
- Ready driver debit on the same page: receipt sync must not enable collection.
- Desktop/mobile: clear no-new-payment wording, usable retry button, no horizontal clipping.
- Mock-only browser checks; no real wallet, payment, production deployment or HA restart.

## Validation and reproduction

From `frontend/driver`, run `npm ci`, `npm test`, `npm run build` and `node build-receipt-sync-preview.mjs`. Serve `preview/receipt-sync` on localhost port 3077, then run `node scripts/qa_receipt_sync.mjs` from the repository root with Playwright available in the QA environment. The fixture uses fictional keys, transactions and a single-leaf proof; its fetch handler rejects external requests. It is not imported by the production bundle.

Validation for this PR: 79 driver tests, 56 operator tests and 713 Python/Home Assistant tests pass. The local full Python suite uses Python 3.14 and the existing BSV/HA test environment; three dependency deprecation warnings remain. Browser QA checks automatic direct/ongoing sync, a ready debit which remains unarmed by sync, manual fallback, wrong identity, declined import, interrupted reporting, late injection, already-acknowledged and unconfirmed receipts.

Do not infer production wallet compatibility or live receipt acceptance from this offline QA. Merge, install and HA restart require a separate deployment approval; no live receipt has been imported as part of preparing this PR.
