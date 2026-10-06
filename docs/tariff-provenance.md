# Tariff provenance

Evidence of which published price was used for each part of a closed session
account. Provenance is **evidence only**. It does not change how amounts are
calculated, which sessions settle, or the account snapshot and its hash.

Addresses part of #11 ("Tariff source/version is retained for independent
reproduction") and #12 ("immutable calculation provenance"). See
[Remaining gaps](#remaining-gaps) for what is still open.

## What is recorded

For each closed (`ended_observed`) sensor-proxy session whose account passes the
normal `account_snapshot` checks, the recorder captures one provenance body
(schema `bsv_settlement.tariff_provenance.v1`) with, per direction (import/Buy
and export/Sell):

| Field | Meaning |
|---|---|
| `source_entity` | HA price entity configured for that direction |
| `intervals[].start` / `end` | Resolved effective interval, ISO 8601 with the original UTC offset |
| `intervals[].published_price` | Price exactly as resolved from the source row (signed decimal string) |
| `intervals[].unit` | Always `$/kWh`. Rows in any other unit are ignored by pricing, as before |
| `intervals[].price_basis` | `final` or `estimate`, as used by the account |
| `intervals[].estimate_flag` | The source row's own flag: `final`, `estimate` or `not_published` (treated as an estimate) |
| `intervals[].retrieved_at` | HA `last_updated` time of the winning observation. Not the provider's publication time |
| `intervals[].source` | The winning normalised source row plus its SHA-256 `row_digest` |
| `intervals[].observations_covering_interval` | How many valid rows competed for this interval |
| `intervals[].status` | `resolved`, `ambiguous` (conflicting rates and no price; `candidate_prices` listed) or `source_row_not_identified` |
| `negative_price`, `zero_price` | Shown explicitly, never hidden |

The body also records the session window, the account's quality flags,
estimated and unpriced Wh, and the names of the selection, allocation and
rounding rules in use. Only intervals that intersect the session window are
kept, capped at 2,000 per direction (`truncated: true` if exceeded).

Resolved intervals come from the unchanged production selector
(`proxy_ledger.select_prices`). Provenance does not reimplement the selection
policy. It identifies the source row that supplied each resolved price.

## Versioning and freezing

- Versions are append-only, per session. A version is
  `{version, captured_at, account_digest, provenance_digest, provenance}`.
- `account_digest` is `sha256(canonical JSON of account_snapshot(record))`. This
  is the same value a manual review stores as `source_hash` and that automatic
  paths compare before payment, so a frozen account identifies its provenance
  version exactly.
- A new version is added only when the provenance body or the linked account
  digest changes, for example a final price replacing an estimate before
  payment. Earlier versions are never edited.
- Each session keeps at most 12 versions. Further changes set
  `version_limit_reached` and are not stored. The newest 200 sessions are kept.
  Older sessions are dropped from the recorder store, never rewritten.
- Wherever a payment record freezes `source_hash`, it also freezes the version
  whose `account_digest` equals it, outside every signed or hashed field:

  | Path | Record | Frozen when | Outside |
  |---|---|---|---|
  | Manual session review | `session_reviews[id]` | `prepare_session_review` | `frozen_terms` / `terms_hash` |
  | Automatic driver collection (single session, reservation, closed-session consent, multi-session child ticket) | `driver_collections[budget_id]` | quote is signed (`collection.py` `status`) | signed `quote` payload |
  | Collection recovery | same record | kept when the quote is rotated | new signed `quote` |
  | Automatic and ongoing operator credits | `automatic_credits[id]` | credit is queued (`auto_credit.py` `process`) | amount, recipient, signed bytes |
  | Original-recipient credit recovery | `automatic_credits[route_id]` | review creates the record | `manual_recovery.terms` / `review_hash` |

- The wallet-ledger record keeps only `tariff_provenance_ref`, a fixed-shape
  reference of about 600 bytes: `status`, `schema`, `version`, `captured_at`,
  `account_digest`, `provenance_digest` (body), `version_digest` (SHA-256 of the
  canonical version JSON), per-direction interval, estimate-interval and
  truncation fields, and `estimated`. Its size does not depend on the number of
  intervals, so the live ledger, its checkpoint digest and its saves stay small.
- The full version goes to the separate `provenance_archive` store (below),
  keyed by `version_digest`. It is written **before** the ledger save that
  references it. Later sensor changes, recorder pruning (newest 200 sessions)
  or new versions cannot alter it, so the evidence outlives the recorder.
- If no version matches (recorder missing, lookup fault, store predates
  provenance, or no version for that exact digest), the reference is
  `{"status": "not_recorded"}`. If the archive write fails or the archive is
  refused, the reference stays and the detail view reports `archive_missing`.
  Nothing is inferred and payment proceeds exactly as before. Neither the
  reference nor the archive feeds amounts, eligibility, recipients, fees,
  limits, guards or broadcast.
- Manual reviews also keep `tariff_provenance: null`. Releases before the
  archive read that field as an inline version, so they show "not recorded"
  instead of failing. Reviews frozen by those releases with an inline version
  are still shown from it.
- `digest_verified` in the detail views recomputes `provenance_digest` from the
  archived body and checks `version_digest` and `account_digest` against the
  reference.

Capture runs after the account is calculated. Any fault in capture is logged and
ignored. It never blocks, delays or changes an account.

## Storage, privacy and compatibility

- Stored in the existing private recorder store
  (`.storage/bsv_settlement.proxy.<entry>`) under `tariff_provenance`. It is
  **not** added to sensor state attributes, so the 16 KB attribute limit and the
  HA recorder database are unaffected.
- Stores and reviews written before this change have no provenance. They show
  `{"status": "not_recorded"}` with a reason. Nothing is inferred or backfilled.
- Frozen references are nested fields inside existing wallet-ledger records
  (`.storage/bsv_settlement.embedded.<entry>`). No ledger namespace or version
  changes: `check_keys` validates top-level namespaces only, and the audit log
  projects record state names only. Older releases ignore the field.
- Full versions are stored in `.storage/bsv_settlement.provenance_archive.<entry>`
  (registry `provenance_archive`, version 1.1, private, atomic, exact keys
  `schema` and `versions`, inner schema `bsv_settlement.provenance_archive.v1`).
  It is owned by the mainnet wallet entry and is append-only: identical
  versions are stored once. Entries referenced by any ledger record are never
  evicted. Up to 50 unreferenced entries (for example after a failed ledger
  save) are kept, and the oldest are dropped first. Each entry is one bounded
  version (at most 2,000 intervals per direction, about 650 bytes per
  interval). Raw observations are never stored.
- A corrupt, foreign or newer archive file is refused like any registered
  store. It is left unchanged and never written. Unlike the ledger, it does not
  stop the wallet loading: new references then show `archive_missing`.
- An unrecognised `tariff_provenance` value is preserved exactly as stored and
  capture stops for that recorder. Nothing is repaired automatically.
- Contents: entity IDs, prices, timestamps and digests. No keys, addresses, HA
  user IDs or credentials.

## Operator view

- `session_review_status` (and the dashboard's latest review) include the
  compact reference as `tariff_provenance`. It is read from the ledger only,
  without touching the archive.
- `session_review_status` with `include_tariff_provenance: true` resolves the
  reference from the archive and returns the full frozen version for
  administrators, with `digest_verified` and the `reference`. It is read-only.
- `session_budget_status` (administrator only) adds `tariff_provenance` when the
  approval has settlement records: `collection`, `automatic_credit`,
  `multi_session_collections` (child tickets of a multi-session approval) and
  `ongoing_credits` (credits routed to this receiving registration, newest 20).
  Compact references by default. `include_tariff_provenance: true` returns the
  archived versions. It is read-only.
- Manual energy-adjustment reviews have no tariff provenance and do not show the
  field. The driver portal, driver receipts and `wallet_status` are unchanged.

## Remaining gaps

- Records frozen before this change keep no reference; they show
  `not_recorded` even while the recorder still holds a matching version.
  Nothing is backfilled.
- The archive shares HA's storage and backup domain. Restoring the ledger
  without the matching archive shows `archive_missing`. Back up both together.
- The staged monthly authority (`monthly_authority.py`, not registered at
  runtime) does not copy provenance. Its binding schema is exact-key validated,
  so adding a field needs a schema change. Until then its `final_account`
  digest can only be looked up while the recorder retains the session.
- Closed-session consent with accepted metering flags freezes a reviewed
  account whose digest the recorder never links (capture links only
  `account_snapshot` digests), so those collections show `not_recorded`.
- Waivers and zero-balance closures are not payments and keep no reference.
- Provenance proves which observed price was used. It does not prove the
  provider published that price. HA history and the provider integration remain
  trusted inputs.
- Post-payment corrections have no reviewed adjustment workflow for tariff
  revisions yet (#11). A later version for an already paid account is evidence
  only. It does not reprice, refund or top up anything.
- Whether estimated prices may finalise an account is an owner decision. See
  [ADR: estimated-tariff finalisation](adr/estimated-tariff-finalisation.md).
