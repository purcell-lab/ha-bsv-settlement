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
- When a manual session review is prepared, the matching version is copied into
  the review record, outside `frozen_terms` (so `terms_hash` is unchanged). Later
  sensor changes, recorder pruning or new versions cannot alter it.
- `digest_verified` in every view recomputes `provenance_digest` from the stored
  body.

Capture runs after the account is calculated. Any fault in capture is logged and
ignored. It never blocks, delays or changes an account.

## Storage, privacy and compatibility

- Stored in the existing private recorder store
  (`.storage/bsv_settlement.proxy.<entry>`) under `tariff_provenance`. It is
  **not** added to sensor state attributes, so the 16 KB attribute limit and the
  HA recorder database are unaffected.
- Stores and reviews written before this change have no provenance. They show
  `{"status": "not_recorded"}` with a reason. Nothing is inferred or backfilled.
- An unrecognised `tariff_provenance` value is preserved exactly as stored and
  capture stops for that recorder. Nothing is repaired automatically.
- Contents: entity IDs, prices, timestamps and digests. No keys, addresses, HA
  user IDs or credentials.

## Operator view

- `session_review_status` (and the dashboard's latest review) include a compact
  `tariff_provenance` summary: status, version, digests and per-direction
  counts (intervals, estimate intervals, ambiguous intervals and negative-price
  intervals).
- `session_review_status` with `include_tariff_provenance: true` returns the
  full frozen version for administrators. It is read-only.
- Manual energy-adjustment reviews have no tariff provenance and do not show the
  field. The driver portal is unchanged.

## Remaining gaps

- Automatic collection, automatic operator credits, monthly and weekly paths do
  not copy provenance into their own records. Their frozen account digest can be
  looked up in the recorder store while that store retains the session (newest
  200).
- Provenance proves which observed price was used. It does not prove the
  provider published that price. HA history and the provider integration remain
  trusted inputs.
- Post-payment corrections have no reviewed adjustment workflow for tariff
  revisions yet (#11). A later version for an already paid account is evidence
  only. It does not reprice, refund or top up anything.
- Whether estimated prices may finalise an account is an owner decision. See
  [ADR: estimated-tariff finalisation](adr/estimated-tariff-finalisation.md).
