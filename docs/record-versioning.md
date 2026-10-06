# Record versioning and transition audit log

A bounded step for #6 (explicit schema versions, migrations, append-only audit
history) and #7 (stale-state detection, restart replay). Development code with
fictional fixtures. It changes no payment, signing, broadcast, recipient or
policy behaviour, and it is not evidence of a protected restore.

## Registry

`custom_components/bsv_settlement/records.py` is the single list of persisted
HA stores. Every store in the integration is constructed via `VersionedStore`
with a registry name; a test fails if a bare `Store(...)` appears or a store
name is missing.

| Record | Storage key | Version | Owner | Policy |
|---|---|---|---|---|
| `coordinator` | `bsv_settlement.<entry>` | 1.1 | `coordinator` | Exact keys `sessions`, `latest` |
| `operator_key` | `bsv_settlement.operator_key.<entry>` | 1.1 | `embedded` | Secret, private, atomic; identity re-derived |
| `wallet_ledger` | `bsv_settlement.embedded.<entry>` | 1.1 | `embedded` | Private, atomic; namespaces below |
| `ledger_checkpoint` | `bsv_settlement.ledger_checkpoint.<entry>` | 1.1 | `ledger_checkpoint` | Inner `version: 1` |
| `wallet_audit` | `bsv_settlement.audit.<entry>` | 1.1 | `audit` | Inner `schema: wallet-audit-v1` |
| `proxy` | `bsv_settlement.proxy.<entry>` | 1.1 | `proxy` | Exact four keys |
| `ocpp_shadow` | `bsv_settlement.ocpp_shadow.<entry>` | 3.1 | `ocpp_shadow` | Converters from 1 and 2 |
| `recorder_reconciliation` | `bsv_settlement.recorder_reconciliation.<entry>` | 1.1 | `recorder` | Inner `schema: 1` |
| `grouped_wallet_test` | `bsv_settlement_grouped_wallet_test` | 1.1 | `grouped_wallet_test` | Exact keys; optional diagnostic |

The mainnet/testnet wallet ledger has 27 registered top-level namespaces
(`LEDGER_NAMESPACES`), each with a primary owner module. `monthly_authorities`
also carries `schema: monthly-authorities-v1`; the ledger carries the
`wallet_checkpoint_version` marker on mainnet. A source scan test requires every
`api.saved["..."]` namespace to be registered. The separate development
`wallet_service` SQLite mock is outside this registry.

Existing files were not rewritten. The HA envelope already records
`version`/`minor_version` for every store; that envelope is the explicit
version. No new in-data marker was added to existing records. Tests confirm a
live-shaped ledger, checkpoint, key and audit file reload byte-for-byte
unchanged, and that envelopes without `minor_version` still load as 1.1.

## Load policy: fail closed, never reset

Before HA parses a file, `VersionedStore` reads the raw envelope. It refuses,
leaving the file in place:

- **Corrupt JSON.** HA would otherwise rename the file to `.corrupt.*` and
  return an empty store, so the next start would silently begin empty.
- **A newer major version.** HA also refuses this.
- **A newer or different minor version without a converter.** HA would
  otherwise pass the data through and rewrite it as the older minor version.
- **Non-integer versions or a missing `data` member.**
- **Unknown top-level keys** in the coordinator, proxy and wallet ledger. The
  envelope stays 1.1 when a release adds a namespace, so an older release must
  refuse the namespace rather than ignore it and overwrite it.

Wallet refusals surface as the existing "restore its matching backup" error and
`ConfigEntryNotReady`. The grouped-wallet diagnostic keeps its existing
behaviour: it logs a warning and stays disabled. There is no reset, clear or
bypass service.

The envelope `key` is not compared, because HA does not read it and fixtures
copy files between entries. Identity binding remains the checkpoint's job.

## Migrations

A migration is an explicit converter on the owning store, with its own tests.
The only converter today is OCPP shadow 1/2 to 3 (`ShadowStore`), validated in
full before HA rewrites the file. Its round trip is tested at the store level:
a version 2 file loads, is written as version 3, and reloads identically. A
store with no converter raises instead of passing data through.

To change a format, bump the registry version, add the converter and tests
with fixtures of the old format, and list it in `migrations`.

**Downgrade hazards.** After a newer release writes a store, an older release
refuses it; this is intended. Code rollback after an audit log exists still
loads, but the older code writes the ledger without appending audit entries.
The next newer load records those differences as transitions observed at load.
An older release that predates the checkpoint writes no witness, so the
checkpoint refuses the next load. Rollback still needs operator reconciliation,
as in [monthly release](monthly-release.md) and
[retained ledger checkpoint](retained-ledger-checkpoint.md).

## Transition audit log

Mainnet only. After each durable checkpointed ledger save, `audit.py` compares
a projection of financial/authority records with the previous projection and
appends one entry per change:

- `payments`, `automatic_credits`, `driver_collections`, `session_budgets`
  (consent and binding), `session_reviews`, `closed_sessions` (closures and
  waivers), `ongoing_credit_routes`, `public_registration_windows` and
  `unallocated_receipts`: created, state change and removed.
- Embedded drafts (`records`), monthly authorities (accepted/cancelled) and
  account bindings, both credit policies (enabled/disabled), the active payment,
  collection-recovery reviews and energy-adjustment requests.

Each entry has: `seq`, `at`, `event` (`baseline|created|state|removed`), `ns`,
`ref`, `from`, `to`, `ledger_sequence`, `ledger_digest`, `state_digest`, `prev`
and `hash`. `hash` is SHA-256 over the canonical JSON of the other fields.
`prev` links to the previous hash, starting at 64 zeroes.

`ref` is a truncated one-way hash of the family and record ID. An operator can
recompute it from a known ID. States are enumerated names only; anything else
is logged as `unrecognised`. Entries hold no keys, signed bytes, addresses,
links, tokens, amounts or user IDs. A test confirms that no ledger or key value
of 16 or more characters appears in the file. The only identifier is the
operator public key that binds the log.

**Retention.** At most 2,000 entries are kept. Older entries are dropped and an
`anchor` keeps the sequence and hash of the last dropped entry. Retained
entries verify from that anchor; the dropped prefix cannot be verified locally.
Archive HA backups if longer history is required.

**Writes.** Appends happen after the ledger is durable and never raise into a
payment path. A failed append marks the log `write_failed` and keeps the
previous projection, so the next save or restart records the missed
transitions. Nothing creates, retries or cancels a payment because of the log.

**Load and verification.**

- Schema, identity, anchor, every link and hash, and the stored projection
  digest are checked.
- A broken, unreadable or newer-version log is reported as `broken`. Its file
  is never rewritten, and settlement continues under the checkpoint.
- An established log that is now absent (config entry marker present) is
  reported as `missing` and is not recreated.
- Status appears in the wallet status under `record_audit`.

**Stale-state detection.** A head is written only after its ledger revision is
durable, so it may lag but never lead. If the retained head names a later
checkpoint sequence, or the same sequence with a different digest, the wallet
refuses to load. This catches a ledger-plus-witness rollback when the audit log
was retained, and legacy re-adoption beneath a newer head. Detection is
precise to the last revision that changed an audited record.

## What this does not prove

- **Coherent full-backup rollback still passes.** If the ledger, checkpoint,
  audit log and config entry are restored together from an older backup,
  every local check agrees. The test
  `test_coherent_rollback_is_explicitly_outside_local_witness_assurance` keeps
  this limit explicit. Freshness needs independently retained evidence and the
  protected restore drill under #4 ([recovery drill](operator-recovery-drill.md)).
- An administrator with file access can rewrite the whole chain consistently.
  The log shares HA's storage and backup trust domain; it is not an external
  append-only service.
- The registry validates versions and top-level namespaces, not every field
  of every record; owners keep their existing record validation.

## Restore-drill helper

`python scripts/inspect_records.py <copied .storage>` reads a copied storage
directory and reports each settlement file as `ok`, `refused: ...` or
`unregistered`. It never writes and contacts nothing. Exit status 1 means
review is required. A clean result is not freshness evidence.
