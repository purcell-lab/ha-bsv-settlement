# Monthly authority, per-session collection

Staged S2 of the [approved workflow](https://github.com/purcell-lab/ha-bsv-settlement/issues/79),
stacked on [S1 / PR #80](https://github.com/purcell-lab/ha-bsv-settlement/pull/80).
Nothing in this change enables the feature, grants a live wallet permission or
creates a transaction.

## Timing and scope

**Collect separately after each charging session closes. Do not accumulate a
monthly bill or wait until month-end.** The recurring 30,000 sat calendar-month
permission is only an aggregate ceiling on those session debits and driver-paid
fees. Each account has one frozen session balance, one settlement owner and one
payment attempt at a time.

A positive net session account can enter the driver debit route. A negative net
session account needs the separately authorised operator-credit route; zero
requires no payment. S2 rejects negative/zero accounts from its debit adapter.
It does not implement a new operator-credit route or change existing recipients.
Incoming credits do not refill the spending limit.

## Implemented internal interfaces

`MonthlyAuthorities` is an internal service over an existing
`SettlementCoordinator`. It is deliberately **not instantiated by integration
startup, registered as an HA service or exposed through an HTTP endpoint**.
Production wallet-period, native-permission and vehicle-presence adapters are
not supplied. Missing adapters fail closed.

- **Issue:** persist an exact version 4, ten-minute acceptance challenge for one
  wallet identity, origin, operator, named station set and reviewed wallet period
  policy. The challenge expiry does not expire an accepted recurring mandate.
- **Accept:** verify the BRC-100-derived wallet signature over canonical monthly
  terms. Repeated proof is idempotent and cannot reset spending or undo cancellation.
- **Bind:** call a trusted owner-verification adapter and freeze recorder/session,
  transaction ID, driver, station, conversion and evidence. A prior current open
  session must be explicitly included in the signed terms; old closed debts are
  not silently included.
- **Reserve:** require a closed, fully priced session using the existing
  `account_snapshot` rules. Freeze the account/hash and require its exact converted
  positive net amount. Reserve that debit and fees within both the app allowance
  and the fresh adapter-observed native remaining allowance.
- **Wallet pending:** recheck the frozen account, grant, period, ownership and
  cancellation; persist the existing operation ID. This is accounting state, not
  a broadcast endpoint or a reusable permission to call the wallet again.
- **Uncertain / commit:** retain the same attempt. Commit is an internal operation
  for independently verified wallet spending, not a driver-supplied success flag.
  It does not imply provider confirmation or wallet receipt acceptance.
- **Cancel:** verify a separate cancellation signature, stop new debit attempts
  and retain reconciliation state. Report native wallet revocation as unverified.

The monthly signature protocol is `[2, "ev monthly spending"]`, with the
authority ID as `keyID` and `anyone` counterparty for verification. It is separate
from the legacy `ev session spending` domain. The payload includes:

- Version, action/scope, authority ID and unique nonce.
- BSV mainnet, exact driver/operator identities and operator destination.
- Exact permission-origin hostname and explicitly named station set.
- Recurring 30,000 sat limit including driver-paid wallet debits/fees.
- Explicit **one final net payment per session on closure** policy.
- Wallet/version period-policy reference, timezone, booking event and fee basis.
- Issue/acceptance times, conversion snapshot policy and optional current session.
- No credit refill and no carry-forward.

Cross-SDK tests sign the exact payload with the TypeScript SDK's `ProtoWallet`
and verify in Python. This is cryptographic interoperability evidence, not a
Metanet or BSV Browser prompt/permission compatibility test.

## Trust boundaries and persistence

Every mutation acquires the same coordinator lock used by legacy operator and
driver routes, checks the expected store revision, and writes a complete
candidate through the existing checkpointed wallet store. In-memory state is
published only after save succeeds. A failed or cancelled save blocks the
monthly service and any replacement instance on the same API until reload.

Strict restore verifies consent signatures, authority/ledger identity, period
timezone, cancellation, bindings, account hashes and cross-record relationships.
Corruption, an unknown schema or a split checkpoint never resets an allowance.
Existing checkpoint limitations still apply: a coherently rolled-back complete
HA backup is not detected by an independent remote monotonic witness.

Monthly binding is mutually exclusive with legacy collection, manual review,
closed accounts, old invitations and frozen ongoing-credit routes. Legacy
session creation/binding, review/closure, weekly child creation and ongoing
recipient assignment refuse monthly-owned accounts. The background legacy
recipient scan skips them rather than assigning a new recipient.

No stored legacy signature, allowance or recipient is migrated automatically.
An existing authority for the same driver blocks creation of a fresh empty
monthly ledger, including after cancellation. A future reviewed amendment flow
must preserve prior spending; it is not implemented here.

## Adapter requirements

These are server-side dependencies, not JSON flags posted by a browser:

| Adapter | Required result | Refusal |
|---|---|---|
| Reviewed period policy registry | Named wallet/version, actual timezone, wallet spend-booking event, fee basis and evidence reference | No assumed UTC or Brisbane default |
| Verified native grant | Exact identity, origin, mainnet, policy, 30,000 sat limit, current month, remaining allowance and fresh evidence | Unknown, revoked, mismatched or stale grant |
| Session ownership | Exact recorder/session and transaction, driver, permitted station, opening time, frozen conversion and presence/binding evidence | Last login or public QR alone |
| Retained recorder account | Authoritative closed-session record under existing metering/pricing rules | Open, changed, unpriced, invalid, zero or credit account in debit route |
| Spending reconciler (future transport caller) | Verify existing wallet operation, exact session payment/output and actual spend/fees before calling internal commit | Browser assertion or replacement transaction |

Wallet grant checks are refreshed after adapter awaits so month-boundary crossings
cannot use an old period. The wallet remains the final signing gate. This server
does not claim that a cached observation prevents concurrent unrelated spending,
reserves funds or permits a disconnected browser to sign.

An unresolved prior-month operation keeps its reservation and blocks new
collection. A wallet booking-period mismatch stays held for reconciliation, not
silently reassigned to a new month. Actual observed fee overruns remain recorded
and block further spending.

## Tests and remaining gates

Offline tests cover Python/TypeScript consent, tampered terms, wrong keys/domain,
expiry, replay, concurrent reservations, revision conflicts, failed saves, strict
restore, cancellation, wrong wallet/network/station, stale grants, month rollover,
legacy ownership exclusion, explicit current-session inclusion, final-session
pricing and duplicate operation/payment evidence.

A real Home Assistant storage fixture writes and restores through the
checkpointed operator store, then injects a split witness/ledger failure and
checks fail-closed restoration. It uses fictional wallet/provider data and does
not contact a live chain or Home Assistant installation.

Remaining S3–S5 work:

- Supply reviewed native wallet adapters and decide origin isolation.
- Expose authenticated, bounded endpoints without exposing internal commit as a
  client-writable status operation.
- Coordinate one-action sign-in, receiving registration and authority setup.
- Wire exactly-once per-session wallet invocation and existing reconciliation;
  repeated reads of `wallet_pending` must never cause another wallet call.
- Integrate the separate credit/zero route for monthly-bound sessions and retain
  immutable receiving metadata.
- Provide the unified UI, cancellation/native-revocation UX, migration/amendment
  workflow and native-device acceptance evidence.
- Separately approve merge, deployment, restart and any real-value test.

Protocol references: [BRC-100](https://bsv.brc.dev/wallet/0100),
[BRC-73](https://bsv.brc.dev/wallet/0073),
[BRC-116](https://bsv.brc.dev/wallet/0116).
