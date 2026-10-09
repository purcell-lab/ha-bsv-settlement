# Automatic-credit confirmation reassessment

This is the first implementation slice for issue #19, not independent chain
verification or a complete commercial-finality policy.

## State meaning

`provider_confirmed` means the configured provider currently reports at least
one confirmation for the exact signed transaction. It is not irreversible
finality, independent header-chain proof or proof of driver-wallet receipt
acceptance. This change does not increase or silently redefine that threshold.

Pending credits retain the existing coordinator reconciliation cadence.
Confirmed automatic and ongoing credits are reassessed at most once per
15 minutes during normal polling. Missing, malformed or future check timestamps
require a fresh check. Opening a receipt forces a fresh read regardless of that
interval.

## Confirmation loss and failures

A verified transaction with zero confirmations returns to
`provider_unconfirmed`. Missing, inconsistent or unavailable provider evidence
returns to `broadcast_unknown`; absence is not proof that a payment failed.
Either state retains the original transaction bytes, transaction ID, source
reservation and account ownership. Neither permits another payment, replacement
or automatic rebroadcast. Disabling credit policy does not stop reconciliation.

Cached proof data is discarded whenever the transaction is reassessed. A new
receipt requires fresh positive confirmation and fresh proof/block data. The
driver still verifies the output and Merkle path against the provider-supplied
root; this is not an independently verified header chain.

## Operational boundary and remaining work

This PR changes read-only evidence assessment, not recipients, caps, fees,
spending mandates or broadcast permissions. It cannot retract a receipt already
imported by a wallet. Confirmation loss after import needs an operator incident
and dispute procedure, not an automatic compensating transfer.

Driver collections now apply the same explicit uncertainty rule when reconciled:
invalid or missing current evidence clears confirmation and retains the original
attempt. See [driver assurance](driver-collection-assurance.md). This does not
add background driver reassessment or change the credit scheduling policy.

Independent header-chain/finality policy, manual-payment parity,
bounded driver and large-ledger scheduling, full receipt provenance
and stale-backup recovery remain tracked by #19, #7 and #22. Normal restarts
must not be confused with a protected backup restore.

Tests use fictional transactions and a recording provider only. They cover
confirmation loss and return, invalid/missing evidence, timestamps, disabled
policy, receipt freshness and retained reservations with exactly one broadcast.

## Operator-approved resubmission of identical bytes

A submission can time out before the provider records it. The credit then stays `broadcast_unknown` indefinitely, and its funding output stays reserved, holding back later credits. The automatic workers still never rebroadcast. An administrator can resolve it in two steps:

1. **`inspect_operator_credit_resubmission`** (read-only). It checks:
   - the stored signed bytes still parse to the same txid, recipient, amount and single funding outpoint;
   - whether the provider has the transaction;
   - whether that outpoint is still in the operator's provider-confirmed unspent set.

   It returns a review hash.
2. **`resubmit_operator_credit`**. It quotes the txid and review hash, plus `confirm_resubmit_identical_signed_bytes: true`, and submits the exact stored bytes again. It is allowed only if:
   - the provider has no record of the transaction;
   - the funding outpoint is still unspent and confirmed;
   - the credit has fewer than 3 earlier resubmissions.

Resubmission never re-signs, replaces, re-prices or redirects anything. The same bytes have the same txid, so the credit cannot be paid twice. Each attempt is recorded in `resubmissions` (time, administrator, outcome) before and after the network call. An accepted resubmission moves the credit to `submitted`, and normal reconciliation continues from there. An uncertain one stays `broadcast_unknown`.

Do not resubmit in either of these cases:
- **The funding output is spent elsewhere or no longer confirmed.** The credit needs separate review.
- **The provider already shows the transaction.** Normal reconciliation will update it.
