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
| `proxy` | `bsv_settlement.proxy.<entry>` | 1.1 | `proxy` | Exact five keys |
| `provenance_archive` | `bsv_settlement.provenance_archive.<entry>` | 1.1 | `provenance_archive` | Private, atomic; exact keys `schema`, `versions`; inner `schema: bsv_settlement.provenance_archive.v1`. Refused files are left unchanged and not written, without blocking the wallet |
| `ocpp_shadow` | `bsv_settlement.ocpp_shadow.<entry>` | 3.1 | `ocpp_shadow` | Converters from 1 and 2 |
| `ocpp_lifecycle` | `bsv_settlement.ocpp_lifecycle.<entry>` | 1.1 | `ocpp_shadow` | Exact keys; inner `schema: 1`; no idTag, only references |
| `recorder_reconciliation` | `bsv_settlement.recorder_reconciliation.<entry>` | 1.1 | `recorder` | Inner `schema: 1` |
| `grouped_wallet_test` | `bsv_settlement_grouped_wallet_test` | 1.1 | `grouped_wallet_test` | Exact keys; optional diagnostic |

The mainnet wallet ledger has 27 registered top-level namespaces
(`LEDGER_NAMESPACES`), each with a primary owner module. `monthly_authorities`
also carries `schema: monthly-authorities-v1`; the ledger carries the
`wallet_checkpoint_version` marker on mainnet. A source scan test requires every
`api.saved["..."]` namespace to be registered. The separate development
`wallet_service` SQLite mock is outside this registry. Entries of the removed
`mock` and `embedded_testnet` backends left files under the same `coordinator`,
`operator_key` and `wallet_ledger` keys; the registry still recognises them,
and setup refuses those entries without reading or rewriting the files.

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

## Restart transition matrices (#7)

A restart reloads the wallet ledger from its HA store into a fresh
`MainnetWalletAPI`. Each cell restarts before and after one transition (or all,
`-all`), replays the same request, and requires one record per session, no
second broadcast or signed transaction, unchanged projection on replay and an
audit history equal to an uninterrupted run (refs compared by first use) whose
chain verifies. Providers are fictional and fail on any extra broadcast.

| Path | Test | Transitions | Cells |
|---|---|---|---|
| Automatic credit | `test_record_versioning.py::test_restart_at_every_credit_transition_retains_one_account_and_its_history` | queue/sign, broadcast, confirm | 1 (restart before each) |
| Manual driver payment | `test_restart_transition_matrix.py::test_manual_review_restart_at_every_transition[driver_payment-*]` | prepare, approve, payment reported, confirmed | 5 |
| Manual operator credit | `…[credit-*]`, `…[credit_unknown-*]` | prepare, approve, credit prepared, broadcast or unknown, confirmed | 12 |
| Manual credit inside broadcast | `test_manual_credit_restart_inside_broadcast_never_resends` | signed not posted, posted not acknowledged | 2 |
| Driver collection | `test_driver_collection_restart_at_every_transition[collection-*]`, `[collection_unknown-*]` | invitation, consent, collection created, claimed, permit, signed reported or unknown, confirmed | 16 |
| Monthly authority (runtime disabled) | `test_monthly_authority_restart_at_every_reachable_transition` | issue, accept, bind, reserve, wallet pending, uncertain, commit, cancel | 9 |

Replays that must not repeat are refused (collection claim/permit, stale
monthly revision); the rest return the stored record. Cancellation inside
collection and automatic-credit calls stays in their interruption matrices.
Limits: monthly attempt states and unused challenges are not in the audit
projection (#103), so the monthly audit comparison covers authorities and
bindings only. A manual credit signed but never posted stays `broadcast_unknown`; the
balance refresh keeps the balance and reports `payment_check_error:
payment_evidence_unavailable` until the provider has evidence (#102).

## Retention and expiry

Every bound below is pinned by a test. Tests without a module prefix are in
`tests/test_retention_bounds.py`. No bound removes a wallet ledger record. The
only deletions from the financial namespaces roll back an insert whose save
failed.

| Store / section | Bound | What is dropped | What is never dropped | Pinned by |
|---|---|---|---|---|
| `proxy` `observations` | Pruned when three or more sessions are derived. Each source keeps one baseline row before the previous session's opening, plus every row since. | Rows before the cut. | The counter baseline, and all rows of the latest and previous sessions. | `test_proxy_archive_keeps_exactly_newest_fifty_and_prunes_to_one_baseline` |
| `proxy` `observations` cap | `MAX_ROWS` = 60,000 per source. | New rows. | Existing rows. Sets the persistent `observation_limit_reached` issue: the recorder is `degraded`, the latest net cost is withheld, and wallet paths that check recorder issues refuse. | `test_observation_cap_is_a_persistent_degrading_issue` |
| `proxy` `archive` | Newest 50 derived summaries, in insertion order, deduplicated by `session_id`. A re-derived session replaces its summary in place. | Older summaries. | The latest and previous sessions, which are never archived. Wallet records bound to an expired session stay. Review, collection, closure, budget, ongoing and monthly sourcing refuse it (for example "not in retained history") and do not reprice it. `previous_session` falls back to `archive[-1]`. | `test_proxy_archive_keeps_…`, `test_rederived_session_replaces_its_archived_summary_in_place`, `test_previous_session_falls_back_to_the_archive`, `test_expired_session_fails_closed_for_review_and_collection` |
| `proxy` `persistent_issues` | Only `observation_limit_reached` and `restart_gap_exceeds_24_hour_backfill`. | Transient issues, which are recomputed on each load or refresh. | Both persistent issues. There is no clear service. | `test_observation_cap_is_a_persistent_degrading_issue` |
| `proxy` `tariff_provenance` | 12 versions per session; 200 sessions, evicting the oldest first capture; 2,000 intervals per direction. | Versions after the 12th (`version_limit_reached`), the oldest sessions, and intervals beyond the cap (marked incomplete). | The session being captured. Copies frozen into manual reviews are unaffected. | `test_tariff_provenance.py::test_version_limit_is_flagged_and_never_overwrites`, `test_tariff_provenance_evicts_oldest_captured_session_beyond_cap` |
| `wallet_audit` | `MAX_ENTRIES` = 2,000. | The oldest entries. | `anchor`: the sequence and hash of the last dropped entry. The retained chain verifies from it. | `test_record_versioning.py::test_retention_is_bounded_and_trimmed_chain_still_verifies` |
| `ocpp_shadow` import section | `MAX_EVENTS` = 1,000, `MAX_SPANS` = 50. | The oldest journal events and closed spans, with `journal_trimmed` and `spans_trimmed` set. | The open span. Shadow data is never financial (`billing_eligible: false`). | `test_ocpp_shadow.py::test_bounded_retention` |
| `ocpp_shadow` export section | `MAX_EVENTS` = 1,000, `MAX_SPANS` = 50. | Same as the import section. The trimmed section still passes `validate_section`. | The open span. | `test_export_shadow_journal_and_spans_trim_with_flags_and_reload` |
| `recorder_reconciliation` | `MAX_RESULTS` = 50. | The oldest results, with `results_trimmed` set. | Lifetime `totals`. | `test_recorder_readiness.py::test_ledger_watermark_retention_and_refusal` |
| Wallet ledger: payments, reviews, budgets, collections, automatic credits, ongoing routes, closures | No expiry. | Nothing. | Everything. | `test_automatic_credit_summary_keeps_old_unresolved_and_caps_resolved` and the display tests below |

**Display windows.** Several status summaries show every unresolved row plus
the newest resolved rows filling to 20, in insertion order
(`summary_window.py`, #105):

- `automatic_credit.payments` (`auto_credit.py`)
- `driver_approvals`, which excludes weekly children (`budget.py`)
- `session_payments`: manual reviews and driver collections, each windowed
  separately (`mainnet.py`)
- `ongoing_credit.sessions`, and the routes per receiving registration
  (`ongoing_credit.py`)

`closed_sessions` is not windowed. Each list has a `{total, shown, unresolved}`
companion (`automatic_credit.payments_window`, `driver_approvals_window`,
`session_payments_window`, `ongoing_credit.sessions_window`). The
`wallet_summary` counts are ledger totals, and its `display_windows` attribute
records the companions. These windows are views only: the tests show that the
saved rows are unchanged and stay reachable through their per-ID status
services.

Resolved means no further payment action can occur. Any other state,
including an unknown one, is unresolved and stays visible:

| Record | Resolved |
| --- | --- |
| Automatic credit | `provider_confirmed` |
| Ongoing route | its credit item is `provider_confirmed`; with no item, `no_operator_credit` |
| Driver collection | `provider_confirmed`; or `ready` (unclaimed) once its approval is `expired`, `revoked` or `charge_waived` |
| Manual review | `cancelled`, `no_payment_due`, `driver_payment_provider_confirmed`; an unpaid `awaiting_account_approval` or `credit_review_approved` review past `expires_at` with no payment request; with a credit draft, the draft is `provider_confirmed`, `expired`, `cancelled` or `cancelled_driver_changed` |
| Driver approval | no unresolved collection or credit under it (weekly parents include their children), and it is `expired`, `revoked` or `charge_waived`, or a non-weekly approval whose own collection or credit is `provider_confirmed` |

A large unresolved backlog makes a list longer than 20 rather than hiding
any of it.

**Expiry of a recorder session.** A session that leaves the latest, previous
and archive views cannot be sourced again. Collection, review approval,
closure or waiver, budget binding and monthly sourcing all refuse it. The
ledger row stays unresolved and nothing is signed, but there is no in-product
completion path. Settle or cancel bound sessions before 50 newer sessions are
recorded.

## OCPP and proxy identifiers

These identifiers are distinct and are never interchanged:

- **Proxy `session_id`**: `sigen-proxy-<uuid5(URL, "<state entity_id>|<opened_at>")>`.
  `opened_at` is the local ISO timestamp of the first preparing or active
  state. The same UUID is `ocpp_transaction_id`, with
  `transaction_id_source: proxy_generated`,
  `native_ocpp_transaction_id: null` and
  `transaction_id_verified_by_charger: false`. It is not a charger
  transactionId. Wallet ownership keys are
  `<proxy config entry id>|<session_id>`. Budget bindings store the proxy
  `{session_id, transaction_id}` pair and never change once set.
- **OCPP `transactionId`**: the native value from the charger. Only the
  read-only shadow ledgers and the lifecycle replay use it. Shadow spans have
  their own `span_id` and are never financial.
- **Lifecycle `SessionIdentity`**: exactly (charger, connector, transactionId,
  start). It is used only in the replay and shadow report
  ([attribution contract](ocpp-lifecycle-replay.md#attribution-contract)). No
  wallet module imports it.
- **idTag**: untrusted metadata, never an identity or ownership key. Only
  one-way `id_tag_refs` are stored.

**Source entity renamed in HA.** Config entries store entity IDs. There is no
options flow or reconfigure flow for the proxy.

- **Proxy.** The proxy reads and tracks the stored `entity_id` strings. After a
  rename, the old ID has no state, and the recorder reports
  `<source>:unavailable`, then `<source>:history_unavailable` after a restart.
  The recorder is `degraded`, the latest net cost is withheld, and every wallet
  path that needs it refuses. The proxy does not follow the new ID. Renaming
  the entity back restores observation. A new proxy entry on the new ID is a
  separate recorder: it has a new entry ID and store, and different session
  IDs, because the ID hashes the state entity_id. Existing wallet rows remain
  bound to the old entry. The proxy pins no registry `unique_id`. If a
  different sensor later takes the old `entity_id`, the proxy observes it
  without an identity check. Only the unit checks in the session derivation
  apply.
- **OCPP shadow.** Each source is pinned by registry `unique_id`, config entry
  and device (`source_binding`). A rename, a moved device or a changed
  `unique_id` makes the running coordinator `incompatible`. It closes the open
  span with `source_binding_changed` and records nothing further. The next
  setup raises and leaves the store untouched. Metadata, export and reference
  bindings degrade to `metadata_binding_changed` or `export_binding_changed`,
  or are ignored. These cases are pinned by
  `test_renamed_proxy_source_degrades_and_is_not_followed` and
  `test_renamed_ocpp_shadow_source_is_incompatible_and_refuses_reload`.

**Boundary-rule changes.** Changes to the open and close rules in
`proxy_ledger.infer_sessions` and `build_records` apply only on replay of
retained observations. That means the latest and previous sessions, and any
later ones. Archived summaries are retained as derived and are never
recomputed under new rules. A re-derived session keeps its ID when its
`opened_at` is unchanged, and replaces its archived summary in place.
Otherwise, a new ID appears and the old summary stays until it ages out.
Frozen records keep their `terms_hash` and `source_hash`. When the current
derivation no longer matches, each consumer refuses:

- reviews: "The session account changed; do not use this frozen review"
- collections: "Session account changed after the quote was frozen"
- monthly: "Retained session does not match its immutable binding"

No consumer reprices the record or pays it again. See also the `Occupied` rule
in [sensor session proxy](sensor-session-proxy.md).

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
