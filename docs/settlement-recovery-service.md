# Reusable guarded settlement recovery

One administrator entry point inspects existing settlement records and chooses
a safe recovery path. It is not a universal retry or “mark paid” command.
Unsupported or uncertain cases return a blocked plan and a next step rather
than guessing, substituting a newer session, or creating another payment.

## Inspect first

`bsv_settlement.prepare_settlement_recovery` takes:

| Field | Purpose |
|---|---|
| `config_entry_id` | Loaded mainnet operator-wallet integration |
| `record_type` | `review`, `budget`, `credit` or `payment` |
| `record_id` | Exact identifier of the record, never “latest” |

Identifier types:

- `review`: session-review or energy-adjustment review UUID.
- `budget`: spending-approval UUID; resolves its original collection or automatic credit.
- `credit`: complete ongoing-credit route ID, or `adjustment:<review UUID>`.
- `payment`: stored operator-payment draft ID, not its blockchain txid.

The response includes observed state, direction when known, amount when
available, txid, provider confirmations, wallet-receipt status, proposed action,
eligibility, blockers and an `expected_recovery_hash`.

Inspection reads retained state only. It does not query the provider, verify
the user's native wallet, refresh the recorder, create a quote, modify expiry,
release a reservation, sign a payment, broadcast or persist a change. A hash
is a stale-state guard, not authorisation.

## Supported decisions

| Existing case | Proposed recovery | Can this service execute it? |
|---|---|---|
| Expired wallet-connected debit adjustment with no collection record | Renew the same amount and recipient for ten minutes; require fresh wallet claim | Yes, after explicit checks and approval |
| Reserved session-budget attempt, no signing permit, original mandate still valid | Existing guarded pre-permit release; retain original budget expiry | Yes, after explicit checks and approval |
| Ready quote, prepared credit, interrupted adjustment attempt | Inspect and continue its original workflow | No automatic reset |
| Signing permit issued | Inspect original wallet/action and retained evidence | No reset or replacement |
| Broadcast unknown or verification error | Reconcile exact existing transaction and receiving history | No resend |
| Submitted/unconfirmed payment | Await/reconcile the original transaction | No replacement |
| Confirmed operator credit, receipt missing | Sync original receipt with original wallet | Wallet action; never another payment |
| Confirmed payment with receipt recorded | No payment recovery required | No-op plan |
| Closed, expired consent, unsupported or conflicting routes | Explain blocker and use separately reviewed consent/account/credit/waiver workflow | No blanket override |

Provider confirmation is not wallet receipt acceptance. Receipt acceptance is
not proof of finality or spendability. An operator's attestations are recorded
as attestations, not independent provider or wallet verification.

## Apply an eligible plan

`bsv_settlement.execute_settlement_recovery` takes the same exact identifiers,
the returned `expected_recovery_hash`, a nonsecret `evidence_reference`, and:

```yaml
confirm_driver_wallet_checked: true
confirm_recipient_history_checked: true
confirm_unsigned_draft_cancelled_or_absent: true
confirm_old_driver_pages_closed: true
confirm_recovery: true
```

Set these only after the checks have actually been completed. The dispatcher
re-inspects the record under the existing coordinator lock, compares the exact
hash, then invokes only one of the two allowlisted non-payment recovery paths.
It never accepts an arbitrary service name or arbitrary replacement recipient.

The downstream guards are repeated at execution. Changed state, signatures,
permits, receipts, revoked registration, expiry conflicts or missing checks
block execution. Future adapters must preserve this inspection-first design
and add tests; an unfamiliar state is not implicitly safe.

The service does not sign or broadcast a payment. The existing pre-permit
recovery can sign a replacement collection quote; a fresh driver confirmation
and the normal wallet draft/signing checks remain required. An eligible
adjustment renewal similarly permits the portal to ask for fresh native-wallet
consent, not to bypass it.

## Audit and privacy

The adjustment adapter retains original frozen terms, expiry, request and
administrator evidence in a bounded private audit trail. Request and session
IDs, tariff, conversion, amount, recipient and weekly budget are preserved.
The existing budget adapter retains its established recovery audit and
original mandate expiry.

Public plans exclude raw transactions, scripts, signatures, private capability
links, attempt tokens and keys. Save failures restore the prior in-memory
adjustment record. Repeating the same recovery request cannot repeatedly
extend an active expiry or release a new attempt.

## Current expired-debit case

The 179 sat debit can be inspected as a `review` record after installation.
Its live expiry and no-attempt observations alone do not complete the native
wallet and receiving-history checks. Preparation of this PR does not renew
that record, clear its hold, initiate another debit or grant driver spending
authority.
