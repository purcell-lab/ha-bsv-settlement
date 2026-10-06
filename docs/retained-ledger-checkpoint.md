# Retained local ledger checkpoint

This is a bounded safety improvement for #4/#7, not complete stale-backup
assurance. A separate private HA store records the digest, local sequence and
operator identity for the embedded mainnet wallet ledger. It contains no
private key, raw transaction, driver link or receiving address.

## Write and load rules

The checkpoint is written before the ledger, using atomic writes for each
file. They are not a single atomic transaction. A failure or cancellation
between writes leaves a mismatch and blocks later writes in that instance.
A fresh load rejects a missing established store, foreign/malformed checkpoint
or digest mismatch. It never repairs the mismatch by deleting a checkpoint,
creating an empty ledger, resetting the counter or broadcasting a replacement.

The config entry and ledger both record adoption of checkpoint version 1.
Fresh setup establishes an initial ledger. A legacy ledger with neither
marker nor witness is adopted once without changing its payment records.
Adoption is a baseline, not proof that the legacy data was current. An existing
operator identity with a missing ledger is rejected, including before adoption.

Snapshots are copied before the first await; witness and ledger therefore
refer to identical data. Identical saves do not cause another disk write.
The coordinator's existing lock remains responsible for business-operation
serialisation; the store lock serialises checkpoint writes.

## Precisely what this detects

- Restoring only an older ledger while retaining its newer checkpoint.
- Removing the ledger, or removing the witness after adoption.
- An interrupted write that leaves witness and ledger on different revisions.
- A checkpoint belonging to a different operator identity or unknown format.

## What it cannot detect

**Restoring the ledger, witness and configuration coherently to an older
snapshot can pass this local check.** The counter is local and is not trusted
monotonic hardware, independent storage or an external append-only audit log.
It also does not protect against an administrator who modifies both files.
This limitation has an explicit regression fixture, not an assumed guarantee.

The [transition audit log](record-versioning.md) narrows one case: if the audit
log is retained while only the ledger and checkpoint are rolled back, its head
names a later revision and loading is refused. Restoring all three together
(with the config entry) still passes.

All post-adoption ledger contents are hashed, but this is not full business
schema validation, a migration framework or proof that chain evidence is
current. Legacy adoption cannot reconstruct records already absent.

## Recovery and deployment

Back up the config entry, matching key, wallet ledger, checkpoint, audit log, coordinator
and proxy records together. Keep an independently retained high-water record
and reconcile signed transactions, inputs and session ownership against current
evidence before enabling a restored production instance. Keep network broadcast
blocked in the isolated restore drill. HA's normal config-entry write scheduling
is not a cross-file transaction; the ledger marker and witness provide redundant
local adoption evidence, not protection against whole-backup rollback.

A rejected load needs an operator-led matching recovery. There is deliberately
no clear/reset/bypass service in this PR. An older integration can write the
ledger without updating the witness, so code rollback after any writes requires
reconciliation; do not promise a blind downgrade/upgrade cycle.

Tests use unfunded fictional identities and providers. No production backup
or live funds are used. #4 and #7 remain open for protected-restore execution,
independent freshness evidence and all payment-path interruption coverage.
