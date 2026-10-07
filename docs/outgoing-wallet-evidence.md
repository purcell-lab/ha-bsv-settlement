# Outgoing wallet evidence: inspection, not repair

R2 of [weekly reliability #91](https://github.com/purcell-lab/ha-bsv-settlement/issues/91).
Depends on the reviewed R1 evidence boundaries. This module is deliberately
not imported by the production entry point and does not run on connection,
sign-in, history refresh or receipt sync. It is a tested capability foundation,
not a completed native-wallet synchronisation fix.

## Findings in the pinned interface

The project's `@bsv/sdk` 2.0.13 wallet interface exposes `listActions`.
It supports label filtering, bounded result counts, and `seekPermission:false`.
The adapter rechecks the exact authenticated identity and mainnet before and
after the query, then accepts only the exact transaction with the exact
`ev-session:<budget-id>` label and outgoing direction.

The same interface documents `sendWith` as sending previously `noSend` actions.
It is not a read-only status notification. `abortAction` changes wallet state.
`internalizeAction` is receipt/output processing, not a proven generic outgoing
status repair. None is used here.

Source inspected: the pinned package's
`src/wallet/Wallet.interfaces.ts`, available in the SDK source tree:
https://github.com/bsv-blockchain/ts-sdk

## Evidence contract

- `wallet_status`: the bounded wallet-reported action status, or `unknown`.
- `evidence: wallet_reported`: not independent provider confirmation.
- `status_repaired: false`: this adapter cannot repair native wallet records.
- `retry_authorised: false`: even missing/failed/nosend records do not permit retry.
- `provider_checked: false`: provider evidence remains separately obtained.
- `receipt_acceptance: not_assessed`: an outgoing record is not an incoming receipt acknowledgement.

Only one page of at most 50 labelled actions is queried. No match means “not
observed in this bounded query”, never “not paid”. Invalid, ambiguous, denied
or unsupported responses remain unknown. Exceptions and extra wallet metadata
are not returned. No raw transaction, scripts or global wallet history is requested.

## Required follow-on gate

Before wiring a native repair into the UI:

1. Identify a wallet-supported method that reconciles the exact existing
   transaction without a new payment, invalidating change or changing ownership.
2. Verify its behaviour against a pinned native wallet/version. Do not infer
   installed support from this SDK type surface.
3. Test provider-confirmed, unconfirmed, missing, conflicting and reorg cases;
   wallet denial, stale records, identity switching and interrupted responses.
4. Review whether the method submits bytes or changes wallet state. Obtain
   separate approval for any live native action; do not relabel it read-only.
5. Keep the server's one-use signing permit, post-signature rechecks and
   exact-byte broadcast ownership intact.

No live wallet test or status repair was performed by this PR.

The next staged [native-wallet acceptance pack](native-wallet-acceptance.md)
wraps this adapter with versioned environment checks, timeout/overlap guards
and a private-target-safe observation. It is still not imported by the shipped
page and cannot repair a native action.
