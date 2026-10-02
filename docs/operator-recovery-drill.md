# Isolated operator-wallet recovery drill

This procedure advances #4 and #7 but is **not an executed recovery result**.
Use unfunded fictional wallet material in a disposable HA test installation
with network broadcast blocked. A production key or a funded backup must not
be copied into CI, a public issue or an agent workspace for this drill.

## Preconditions and stop conditions

- Assign a recovery operator and an independent witness before execution.
- Inventory the supported HA/integration versions and the intended identity
  anchor, config entry, key store, account/mandate/route stores and signed-input
  reservation records. Store identifiers belong in private evidence only.
- Block network egress to broadcasters and use a recording fake provider.
  Integration flags alone are not an independent safety boundary.
- Preserve a verified read-only copy of the fixture and its checksum. Never
  overwrite the only copy while testing recovery.
- Stop on identity mismatch, unreadable store, missing payment record, ambiguous
  chain evidence or any unexpected signing/network-write attempt.

## Matching restore

1. Create an unfunded fictional operator identity and representative open,
   queued, signed-uncertain and confirmed fixture records.
2. Capture the identity anchor and record fingerprints privately. Capture
   expected session ownership, exact transaction bytes/IDs and reservations.
3. Back up matching HA configuration and all required stores together using
   the supported HA backup facility. Record protection, access and recovery
   instructions without publishing a key or backup secret.
4. Restore into a separate disposable installation, still egress-blocked.
   Confirm the identity anchor matches and no new key was silently generated.
5. Inspect every fixture account and reservation. Reconciliation may read the
   fake provider; it must not sign, broadcast, release a signed input or create
   a replacement payment.
6. Restart the restored instance and repeat comparisons. Record zero network
   writes and unchanged immutable records.

## Required negative cases

| Restore fixture | Expected result |
|---|---|
| Existing identity anchor but missing key store | Load fails closed; no rotation |
| Different key, wrong network or corrupt identity | Load fails closed without secret disclosure |
| Matching key but missing payment/route store | Recovery gate fails; do not enable broadcast |
| Ledger snapshot older than a signed attempt | Stop for external evidence reconciliation |
| Provider reports original input spent | Reservation remains; no attempt to spend it again |
| Provider missing/stale/contradictory | Unresolved state, not a claim of unpaid or safe-to-retry |
| Cached receipt for a disappeared/reorged transaction | Confirmation reassessment required |

The current identity anchor detects missing/changed keys. It does **not by
itself** prove that restored ledger state is current. Stale-restore detection
needs trusted retained transaction/audit evidence and must remain an open
acceptance item until implemented and tested.

## Acceptance and production boundary

Record the fixture checksum, exact versions, comparisons, blocked network-write
count, negative-case results, witness and cleanup in the acceptance run template.
Delete disposable fictional copies only after evidence is retained.

Closing #4 requires actual execution and protected restoration evidence, not
the existence of this document. A production restore is a separate consequential
operation requiring explicit scope approval, a current backup, a stop plan and
reconciliation of all funds and unresolved transactions before any broadcast
authority is re-enabled.
