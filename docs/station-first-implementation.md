# Station-first implementation series

Approved workflow: [issue #79](https://github.com/purcell-lab/ha-bsv-settlement/issues/79).
The driver spending limit is **30,000 sat each calendar month, including
driver-paid fees**, recurring until cancelled. This is a design and development
decision, not live financial authority.

**Collection is per session, not monthly.** Each closed session settles its own
final net account. The monthly allowance only limits cumulative driver spending
and fees; it does not delay payment or combine sessions into a monthly bill.

## One coordinated delivery

Use one parent issue and sequential reviewable PRs. Each PR carries its slice ID,
links this document and records evidence plus remaining gates. Never close #79
from an intermediate PR. Do not create empty placeholder PRs or merge partial
activation into production.

The S1 foundation is deliberately not imported by runtime code. The existing
session/weekly authority, operator-credit policy, conversion sensor, recipients
and driver page remain unchanged.

S2 adds the [internal signed-authority and durable-ledger service](monthly-authority.md)
plus shared ownership exclusions. It is not constructed by runtime setup and has
no public endpoint. Native wallet and physical ownership adapters remain required.

| Slice | Proposed PR title | Base and code ownership | Completion evidence |
|---|---|---|---|
| S1 | Station-first monthly contract and allowance foundation | main; this document, approved workflow, `monthly_allowance.py`, offline tests | Explicit 30,000 sat default, fee-inclusive boundary tests, no runtime activation |
| S2 | Persist monthly consent and exclusive session ownership | S1 after merge, or explicit stacked base; new monthly adapter with small integration points in `budget.py`, `collection.py`, `mainnet.py`, `weekly.py` | Cross-SDK signed terms, durable replay/concurrency, cancellation and identity/binding tests |
| S3 | Coordinate wallet sign-in, receiving and monthly approval | S2; `portal.py`, new frontend monthly wallet orchestrator, capability fixtures | One app action; missing-only prompts; fail-closed capabilities; receipt recovery across owner history |
| S4 | Unify Station, My charging and History | S3 and #78; `frontend/driver`, shared account projection, operator card selectors | Mock previews; mobile, keyboard and dark/light checks; matched account values; QR text/copy |
| S5 | Validate migration, recovery and pilot release | S4; tests, reproducible builds, migration/rollback docs | Failure matrix, native-wallet evidence and separately approved deployment/pilot |

If S2–S5 are developed before their parent merges, base each PR on its immediate
parent branch. Rebase/retarget after merging that parent. Do not merge a child
whose parent is absent. Runtime monthly activation remains off through the series
until all release gates pass.

PR #78 is the existing provisional-account display correction, not a competing
redesign. Keep it separate and consume its projection in S4 rather than redoing
or silently merging it.

S3 status: transport and orchestrator staged on `feat/station-monthly-wallet-setup`;
see [monthly wallet setup](monthly-wallet-setup.md). Still off at runtime.

## Approved interaction contract

Public station information comes before wallet setup: recognised station and
connector, live buy/sell rates and negative-rate explanations. A permanent public
QR does not claim a vehicle or reveal private invitation capabilities.

**Authorise monthly charging** starts the coordinated setup. Its visible terms
name the operator, authorised stations, 30,000 sat monthly cap including fees,
recurrence, credit treatment and cancellation. Authentication, receiving metadata,
wallet monthly permission and signed application consent remain distinct security
checks behind that one action.

After setup, automatically show an unambiguously owned current session and sync
all eligible incoming receipts for the authenticated receiving identity. Show
current rates, OCPP status, directional kWh, average AUD/kWh and provisional net
AUD/sat. Compact history expands technical details; it is not required to receive
credits. Only exceptions need another action.

The primary navigation is **Station · My charging · History**. The wallet drawer
shows identity, connection, permissions, allowance and cancellation without making
those separate routine tasks. Wallet prompts remain under the wallet's control.

## S1 accounting model and limitations

`monthly_allowance.py` is an immutable, pure domain model. It does not verify
signatures, store data, authenticate a driver, call a wallet, create transactions,
register services or change any live policy.

- `Allowance`: aggregate debit ledger for one verified monthly authority.
- `Month`: explicit calendar month in the wallet timezone supplied by the future
  verified adapter. There is no default timezone.
- `Attempt`: one account reservation with an idempotency key and local wallet
  operation correlation ID.
- `Summary`: application Limit, Spent, Reserved and Remaining. This is not a query
  of the wallet's native balance or permission.
- `reserved`: persisted candidate for a proposed debit plus fee headroom.
- `wallet_pending`: must be persisted before calling the external wallet.
- `uncertain`: retain the original reservation and block new spending.
- `committed`: independently verified wallet spending; not chain confirmation.
- `released`: only an uninvoked reservation can use the basic release operation.

The model rejects negative/non-integer amounts, duplicate active accounts,
conflicting idempotency keys, cross-period signing and timezone changes. Incoming
credits have no allowance-refill operation. Committing actual spend replaces its
reservation instead of counting both. Recorded fee/spend overruns remain visible
and block further use, including across months.

Any unresolved attempt from another month conservatively blocks new spending.
If observed wallet booking belongs to a different month than its reservation,
retain the hold and route to the S2 reconciler; never silently move or retry it.
Do not clear uncertainty at month rollover.

No persistence or concurrency guarantee is claimed by S1. In particular, callers
must not apply two transitions to the same stale ledger snapshot and submit both.
S2 must serialize the persisted compare-and-update and reject revision conflicts.
Legacy/manual/other-authority payments must share a global account exclusion, not
only this model's within-authority check.

## S2 authority and persistence contract

Before enabling any monthly signing endpoint:

- Define a new signature domain and versioned canonical payload. Sign authority
  ID, nonce, wallet identity, operator identity/destination policy, exact origin,
  station scope, 30,000 sat limit including fees, recurring terms, effective time,
  wallet period policy identifier, conversion policy and cancellation semantics.
- Verify proof-of-control, signature, replay protection and exact payload equality
  on the server. Reject old session/weekly consent as monthly permission.
- Record wallet capability evidence independently from the application's signed
  mandate. A posted boolean must never establish a wallet grant.
- Persist ledger and exclusive account ownership atomically with a revision,
  before wallet invocation. Use the existing collection attempt for retries of
  status/reconciliation, never create another payment after ambiguous failure.
- Bind only with verified connector/session ownership. Do not claim the next
  vehicle because a wallet was the last to sign in. Existing current-session
  matching requires explicit ownership and no competing payment route.
- Check identity, origin, station, cancellation and aggregate remaining allowance
  again before signing and first submission.
- Freeze conversion per bound session and preserve historical accounts/recipients.
- Implement validated serialization/schema migration and restart restoration.
  Corrupt or unknown versions fail closed, never reset to an empty allowance.
- Keep driver debit and operator credit policies independent. Five-kWh manual
  adjustments are not silently included in charging consent.

## Wallet evidence gates

Implementation may proceed using fixtures, but claims of aligned automatic
monthly operation require real capability evidence for each supported version.

| Gate | Required evidence | Owner / effect |
|---|---|---|
| Origin isolation | Exact permission origin; dedicated-origin or explicitly reviewed shared-origin scope | Engineering/operator; blocks live recurring grant |
| Month semantics | Wallet timezone, calendar rollover, spend-booking event and fee inclusion | Wallet adapter; blocks activation and automatic rollover |
| Spending authority | Native monthly permission request/query/revoke behaviour or explicit per-payment fallback | Wallet adapter; blocks “automatic ready” |
| Receiving | Existing-payment import and signed acceptance for original identity | Wallet adapter; blocks “received in wallet” |
| Session ownership | Safe connector-to-session binding, wrong-driver rejection | Recorder/identity; blocks automatic account claiming |
| Charger control | Tested start/stop and budget-exposure safety | Separate #16; not part of this UI release |

References: [BRC-73](https://bsv.brc.dev/wallet/0073),
[BRC-116](https://bsv.brc.dev/wallet/0116),
[BRC-100](https://bsv.brc.dev/wallet/0100),
[BRC-29](https://hub.bsvblockchain.org/brc/payments/0029).
Do not infer deployed wallet support from the existence of a standard.

## Integrated failure and migration matrix

S5 must link evidence for each case, with mock/offline/native labels:

1. Fresh driver, returning driver, locked wallet and expired portal login.
2. Missing API, declined grouped permission, monthly grant unsupported.
3. Wrong network/identity, expired pairing, browser closed before/during signing.
4. Two simultaneous stations and two simultaneous browser tabs competing for
   the same remaining allowance or the same account.
5. Fees at the limit, unexpected booked overrun, insufficient wallet funds.
6. Negative buy/sell prices, both energy directions, zero and stale estimates.
7. Month boundary before signing, during signing and after uncertain submission.
8. Cancellation before claim, between claim/signing and after submission.
9. Old weekly consent, new monthly consent and existing manual collection collide.
10. HA restart at every persisted payment transition; replay of identical requests.
11. Provider-confirmed but wallet-not-accepted credit; original identity reconnect
    imports all eligible receipts without redirecting to a newer driver.
12. Unknown broadcast, reorg/provider outage and duplicate acknowledgement.
13. Public QR, private link, copying payload, mobile and keyboard navigation.
14. Disable monthly feature without deleting holds or re-enabling legacy collection
    for already-owned accounts.

Native mainnet testing needs separately reviewed recipients, amounts and fees.
No deployment, restart, release tag, payment or wallet-permission increase is
authorised by publishing this series.
