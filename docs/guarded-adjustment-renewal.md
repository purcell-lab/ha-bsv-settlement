# Guarded renewal of an expired wallet adjustment

This path applies only to an expired, wallet-connected debit adjustment that
has never created even a ready wallet-collection record. It is not recovery
for a reserved draft, signing permit, submitted transaction or uncertain
payment. It does not convert legacy manual payment requests.

## Preparation

An administrator calls `bsv_settlement.prepare_adjustment_renewal` with
`config_entry_id` and `review_id`. This read-only call returns the original
amount, receiving address, expiry, total ceiling and an exact-state review
hash. It performs no refresh, signature, broadcast, persistence or expiry
change. Current verified receiving registration must still match the original
driver; there is no recipient substitution.

## Evidence and execution

Before renewal, inspect the driver's native wallet for any payment or unfinished
action, inspect the operator's receiving history, and close old driver pages.
Absence of a backend txid alone is not proof that no external payment exists.
Cancel or confirm absence of any unsigned wallet draft. Do not confirm any
check that has not actually been completed.

After separate operator approval, call `bsv_settlement.renew_expired_adjustment`
with the reviewed `expected_review_hash`, a nonsecret `evidence_reference`,
and all five explicit confirmations described by the service metadata.
The service repeats its eligibility checks under the coordinator lock.

The same review, request index, session reference, amount, tariff, conversion,
driver and operator destination remain in place. Only the expiry in the
frozen terms and request is renewed for ten minutes. The old frozen terms and
request are retained in a bounded private audit trail. The new terms hash
invalidates old claims; no wallet attempt is cleared, and fresh driver wallet
consent is required before an unsigned draft and one-use signing permit.

Renewal itself cannot sign or broadcast. Once the portal is reopened, its
existing automatic queue can request native-wallet approval. The original
1,000 sat fee-inclusive adjustment ceiling remains unchanged; weekly charging
authority is untouched.

## Fail-closed cases

- Any collection record, including an unsigned ready quote.
- Any receipt, credit draft, signed transaction or txid.
- Changed, revoked or expired receiving registration.
- Changed account, tariff, amount, destination, request or frozen terms.
- Non-expired, closed, legacy-manual or non-debit records.
- Stale review hash, missing administrator/evidence confirmations, or audit limit.

Save failure restores the previous in-memory record. Repeating a completed
renewal does not extend its new expiry. This service never automatically renews
another expired request.
