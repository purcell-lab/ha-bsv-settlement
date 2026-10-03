# Original-recipient operator-credit recovery

An unpaid historical route retains the receiving registration selected for that
session. Do not substitute the globally configured driver address or the newest
driver, and do not re-enable all automatic credits to force one old payment.

## Prepare, review, then send

An authenticated HA administrator calls `bsv_settlement.prepare_operator_credit_recovery`
with `config_entry_id` and the historical `credit_id`. The service:

- Verifies the original signed receiving registration and immutable route.
- Checks the closed account, conversion rate, quality rules and exclusive session ownership.
- Builds an unsigned transaction using one confirmed operator output.
- Freezes the original recipient, credit, provider-quoted fee, selected input, change,
  energy account and a ten-minute review hash.
- Holds the route at `credit_review_required`. Restarting HA or enabling the
  prospective automatic-credit policy does not release this hold.

The total credit plus fee cannot exceed 1,000 sat. Preparing does not sign,
broadcast, enable a policy, change a driver registration or reserve chain funds.
Other legitimate payments may spend the reviewed input; if that happens,
approval fails rather than silently using a different transaction.

After reviewing the exact recipient and amount, the administrator may call
`bsv_settlement.broadcast_operator_credit_recovery` with:

```yaml
config_entry_id: "<mainnet wallet entry>"
credit_id: "<original historical route>"
expected_review_hash: "<hash from preparation>"
recipient_address: "<original BRC-29 receiving address>"
amount_sats: 119
fee_sats: 23  # Example only: repeat the actual fee from the prepared review.
confirm_mainnet_payment: true
```

These are documentation placeholders, not an executable live instruction.
The master credit policy and wallet broadcast switch must be enabled. The
standing ongoing-credit policy need not be enabled for this exact one-off payment.
Original account, registration, reviewed input, session exclusions and limits are
checked again before signing and submission.

The temporary execution permit is never persisted. A failed unsigned execution
stays held, including after restart. Signed bytes and input reservation are saved
before network submission; a repeated request returns the existing result and
never re-signs or rebroadcasts it.

## Receipt and prospective automatic payments

The payment stays in the original automatic-credit outbox and original receiving
budget. Its existing driver receipt endpoint supplies the BRC-29 derivation
metadata, energy account and confirmed transaction proof for wallet import.
An uncertain transaction cannot supply a confirmed receipt or be replaced.

Prospective ongoing payments remain a separate administrator-authorised policy.
They select the latest verified receiving registration at session opening, use
the configured fixed conversion rate, and retain the 1,000 sat total cap including
the live size-based fee. Enabling the policy without `initial_session_id` does not sweep
historical sessions. Existing routes from an earlier policy activation do not
inherit the new authority.

## Expiry and audit

Calling prepare again returns the same review without extending expiry.
To renew an expired unsigned review, supply its exact
`replace_expired_review_hash`. This creates newly reviewed terms and requires
new explicit payment approval. Changed frozen accounts, signed transactions and
conflicting settlements cannot be replaced by this action.

This remains a sensor-proxy proof of concept, not certified energy billing.
Provider confirmations are not independent proof of finality.

The fee is frozen for review, not silently raised on approval. If the live quote
increases beyond the reviewed fee, prepare a new review after expiry and obtain
fresh exact approval. Signed transactions are never repriced or rebroadcast.
