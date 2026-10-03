# Collection recovery validation

All data and wallet keys in the browser preview are fictional. No live collection
was released, retried or paid during these checks.

## Coverage

- First failure: retains the named collection stage separately from a later
  connection-refresh error.
- Held attempt: no payment retry button and no automatic wallet draft.
- Reviewed recovery: an explicit driver confirmation displays amount and fee cap.
- Cancel confirmation: no claim, draft, signing or submission.
- Confirm recovery: one simulated claim and draft request. A failed draft records
  its stage and returns to a held state.
- Repeated polling after failure: no second claim or draft.
- Operator: Payments displays the held session and marks its diagnostic as
  driver-reported, not verified payment evidence.
- Browser checks: 375 px mobile and 1280 px desktop, light and dark modes.
  No horizontal page overflow or uncaught JavaScript errors observed.
- Backend: capability and attempt-token checks, administrator-only service
  context, stale quote rejection, retired-token rejection, fresh driver signature,
  original expiry, frozen account, revocation, persistence failure and cancellation,
  audit retention limit, and refusal after any permit or transaction evidence.
- Existing payment protections: exact draft checks, fee/recipient limits,
  no-send signing, and exact-byte reconciliation tests remain passing.

## Reproduce locally

```sh
python -m pytest -q
cd frontend/driver
npm ci
npm test
npm run build
node build-recovery-preview.mjs
cd ..
npm ci
npm test
npm run build
cd ../preview
python -m http.server 8772
```

Open `/recovery/index.html` for the driver page. Use the scenario selector to
test the held attempt and reviewed recovery. The offline scenario allows the
first read, then fails subsequent reads; wait 30 seconds to see the separate
connection banner while the original diagnostic remains visible.

Open `/` for the operator page, select **Driver collection interrupted**, then
**Payments**. Preview requests are simulated and no HA service is contacted.

The driver fixture blocks all fetch requests other than its in-memory API,
including local wallet discovery probes. Its fake wallet always rejects draft
creation and cannot sign or broadcast a transaction. It is not included in the
installed HACS bundle.

## Limits

These tests do not establish the original cause of an old `Failed to fetch`
message or validate a particular native wallet's connectivity. Live recovery
requires separate deployment authorisation and external evidence review as
described in [the recovery procedure](collection-recovery.md).
