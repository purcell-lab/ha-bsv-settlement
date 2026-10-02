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

Independent header-chain/finality policy, manual-payment and driver-collection
reassessment parity, bounded large-ledger scheduling, full receipt provenance
and stale-backup recovery remain tracked by #19, #7 and #22. Normal restarts
must not be confused with a protected backup restore.

Tests use fictional transactions and a recording provider only. They cover
confirmation loss and return, invalid/missing evidence, timestamps, disabled
policy, receipt freshness and retained reservations with exactly one broadcast.
