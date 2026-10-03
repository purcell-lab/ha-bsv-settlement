# Safe operator-credit confirmation handling

## Problem

The chain provider can return a complete unmined transaction without confirmation
or block fields. The operator-credit reconciler previously treated this as invalid
evidence, displaying `broadcast_unknown` even when the transaction was visible.
PR #40 corrected the driver-collection path but not operator credits.

## Acceptance rule

The operator-credit path now applies the same narrow unmined-record rule:

- Provider raw bytes must exactly match the persisted signed transaction.
- Parsing those bytes must produce the expected transaction ID.
- The provider transaction ID and hash must match that ID.
- The confirmation field must be absent, not explicitly null or malformed.
- No block hash, height or time field may be present.
- Integer version, locktime and byte size must match the transaction.
- Input and output lists must exist and have matching counts.

Only this combination produces `provider_unconfirmed` with zero confirmations.
The existing interface then displays “Awaiting block confirmation”. This is a
provider observation, not independent proof of finality.

Explicit non-negative integer confirmation counts retain their existing handling.
Malformed responses, incomplete records, conflicting unmined metadata, provider
outages and raw transaction mismatches remain blocked as `broadcast_unknown`.

## Safety boundaries

Both per-session and ongoing operator credits use the corrected reconciler.
Existing signed transactions are reconciled in place on the next normal worker
check; no replacement, new signature or rebroadcast is introduced.

The transaction, destination, amount, fee, frozen account and input reservation
remain unchanged. Stale errors clear only after valid evidence. Cached receipts
are removed on reassessment, including a return from confirmed to unconfirmed.
Wallet receipt import still requires provider confirmation and the existing proof
checks. Confirmed-funding selection is unchanged.

## Activation

This Python change requires approved installation and a Home Assistant restart.
No frontend rebuild or data migration is required. Until deployment, the existing
credit path continues to use its previous confirmation handling.

Tests use fictional keys and a recording fake chain provider. Live funds, wallet
permissions and automatic-payment policy are not changed by this PR.
