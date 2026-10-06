# Recorder readiness and legacy reconciliation

This is the read-only recorder contract from the
[OCPP recorder investigation](ocpp-recorder-investigation.md) (issue
[#61](https://github.com/purcell-lab/ha-bsv-settlement/issues/61)). It reports how far each
recorder's evidence goes, per direction. It also compares every closed
[OCPP import](ocpp-import-shadow.md) and [export](ocpp-export-shadow.md) shadow
span with the legacy Sigenergy recorder over the same Home Assistant time
window. There is **no source selector and no switching**. The legacy recorder
remains the only settlement source.

## Safety boundary

- **Session recorder** is always `legacy_sigen` when a legacy recorder is linked.
  It is read from configuration. No service, select or switch can change it, and
  this change registers no new services.
- Readiness is not a payment-authority indicator. `billing_eligible` is `false`
  on every new sensor and stored result, and `settlement_owner` is
  `legacy_sigen`.
- The financial modules (budget, collection, ongoing credit, session closure,
  session review, weekly approvals, portal) are unchanged. They still accept only
  `mode == "sensor_proxy"`, so OCPP evidence, readiness or reconciliation can
  never become a payment record.
- Nothing is written to either recorder or to the OCPP integration. No charger
  command and no OCPP configuration change is made.

## Contract

`custom_components/bsv_settlement/recorder.py` defines a source-neutral,
read-only contract:

| Item | Values |
| --- | --- |
| `recorder_kind` | `legacy_sigen`, `ocpp` |
| `role` | `settlement_source` (legacy) or `shadow` (OCPP) |
| `health` | legacy: `ready`, `stale`, `degraded`, `unavailable`; OCPP: `shadow_only`, `unavailable`, `incompatible` |
| per direction (`import`, `export`) | `level`, `limitations`, `entity_id`, `last_sample_at`, `sample_age_s`, `expected_cadence_s`, `freshness_limit_s` |
| `overall_level` | the lowest direction level |

`LegacySigenRecorder` wraps the existing `ProxyCoordinator` and
`OCPPShadowRecorder` wraps the OCPP shadow coordinator. Both are facades: they
only read coordinator state, HA states and the entity registry.

## Readiness ladder

Each direction is placed on this ladder:

1. `unavailable`: there is no entity, it is disabled or unbound, or the binding
   changed. OCPP export stays here until it is bound (`export_not_bound`).
2. `entity_enabled`: the entity exists but the reading is unknown, unavailable
   or non-numeric.
3. `numeric_reading`: there is a finite, non-negative reading in a supported
   unit, but it is stale or not attributable.
4. `fresh_attributable_sample`: the sample is fresh **and** the recorder accepted
   it for this connector and direction:
   - OCPP import: the import shadow has an open span with no blocking flag
     (active transaction, `Sample.Periodic`, not restored, bound connector).
   - OCPP export: the export shadow has an open span with no blocking flag.
   - Legacy: the Sigen running state is known and the proxy has no
     observation-limit issue.
5. `validated_directional_accounting`: this level is **never reached
   automatically**. It needs a persisted reconciliation result within tolerance
   for that direction, an explicit operator validation flag and non-estimated
   evidence. This change provides no operator flag
   (`OPERATOR_VALIDATION_AVAILABLE = False`). Adding a reviewed,
   operator-confirmed validation record is future work.

A sample is **fresh** while its age is at most
`max(3 × cadence, cadence + 30 s)`. Age is measured from the later of the
entity's `last_updated` and `last_reported`. Cadence is 5 s for the legacy
Sigen counters, giving 35 s. For OCPP it is the charger's
`MeterValueSampleInterval` from provenance, or 60 s by default, giving 180 s; an
unknown interval adds `sample_interval_unverified`. A stale sample or a sample
more than 5 s in the future drops the direction to `numeric_reading` with
`stale_sample`. OCPP outside a transaction is therefore `numeric_reading`, not
ready.

Derived OCPP export (`source: derived_from_negative_import`) carries
`export_estimated` and can at most reach `fresh_attributable_sample`, even with a
reconciliation result and an operator flag. Native export data is labelled
`native_export_unverified`. OCPP import always lists `entity_observation_only`,
`source_timestamp_unavailable` and its provenance flags. Below the top level,
every direction lists `directional_accounting_not_validated`.

## Aligned-window reconciliation

When an OCPP import or export span closes, its energy is compared with the
legacy register delta for the same direction: the Sigen charge counter for
import and the discharge counter for export. The comparison window is the span's
first and last accepted meter sample, in HA arrival time.

Legacy method, from the proxy's recorded cumulative readings (MWh, converted to
kWh with Decimal):

- **Brackets.** At each window edge, find the nearest reading at or before the
  edge and the nearest reading after it. The current legacy state, at its last
  report time, counts as a reading after the latest change.
- **Change-only rows.** The proxy stores only value changes. Sigen reports about
  every 5 s, so a step of at most 25 kW × 5 s + 10 Wh is assumed to have happened
  within one cadence before the row that shows it. A larger step means reporting
  was not observed, and the whole bracket is used.
- **Point estimate.** Interpolate linearly inside the effective bracket.
- **Bounds.** Use the bracketing readings ± half the 10 Wh resolution:
  `lower = (before(end) − 5 Wh) − (after(start) + 5 Wh)` and
  `upper = (after(end) + 5 Wh) − (before(start) − 5 Wh)`.
- **Precision.** The legacy counter's 10 Wh resolution and ~5 s cadence bound
  the comparison. Even with perfect brackets the legacy delta is uncertain by
  about ±10–20 Wh. HA arrival times on both sides also add the two integrations'
  delivery latency.

`allowance = max(tolerance % × legacy estimate, absolute floor)`.
`difference = OCPP − legacy estimate`. These checks are evaluated in order:

| Explanation | Outcome | Rule |
| --- | --- | --- |
| `ocpp_missing` | unresolved | OCPP span energy or window unusable |
| `ocpp_span_partial` | unresolved | fewer than 2 samples, zero-length window, or the span ended other than at a transaction/lifecycle boundary (gap, restart, invalid/stale meter, decrease, binding change ...) |
| `legacy_missing` | unresolved | no legacy recorder linked/loaded within 15 min, or no legacy reading before the start or after the end |
| `legacy_counter_invalid` | unresolved | invalid unit/value or a decrease inside the bracketed span |
| `misaligned_window` | unresolved | an edge's effective bracket is longer than 60 s while the counter moved inside it |
| `insufficient_energy` | unresolved | both energies are below the minimum (default 0.1 kWh) |
| `legacy_resolution_limited` | unresolved | half the legacy bound width exceeds the allowance |
| `aligned` | within tolerance | `|difference| ≤ allowance` |
| `ocpp_bounds_contain_legacy` | within tolerance only if the export span's grade is `within_tolerance`, else unresolved | export only: the legacy estimate lies within the OCPP lower/upper bounds |
| `difference_exceeds_tolerance` | outside tolerance | otherwise |

If either source is missing, the result is unresolved, never "ok". Partial
spans still record their numbers for information. A span is reconciled once, at
least 20 s after it closes so that a legacy reading exists after the window.
Spans that closed while no legacy recorder was linked are skipped and counted
(`skipped_not_linked`), not reconciled later. Spans that closed before the store
was created are not reconciled either.

Each result stores: comparison and span IDs, direction, native transaction ID,
window start/end/seconds, span end reason, sample count, OCPP kWh (with export
lower/upper and grade), legacy entry ID and status, legacy kWh/lower/upper/edge
gaps/resolution, legacy proxy flags, difference kWh and %, allowance, the
tolerance used, `within_tolerance`, outcome, explanation and time.

### Tolerances

Options on the OCPP shadow entry, stored as `reconciliation_tolerances`:

| Option | Default | Range |
| --- | --- | --- |
| `reconcile_import_tolerance_pct` | 1 % | 0.01–100 |
| `reconcile_import_floor_kwh` | 0.02 kWh | 0–10 |
| `reconcile_export_tolerance_pct` | 5 % (as export grading) | 0.01–100 |
| `reconcile_export_floor_kwh` | 0.02 kWh | 0–10 |
| `reconcile_min_energy_kwh` | 0.1 kWh | 0.001–100 |

On the live site the OCPP import register tracks the Sigen charge counter
within ±0.03 kWh. Derived export differs from the discharge counter by
0.7–4.5 %, which the bounds and the export grade explain.

## Configuration

The link is option `legacy_proxy_entry_id` on the OCPP shadow entry:

- **Absent** (existing entries that have not been reconfigured): if exactly one
  `sensor_proxy` entry exists it is auto-detected (`link_state: auto_detected`).
  With none or several, nothing is linked (`no_legacy_recorder` / `ambiguous`).
- **Configure:** after the metadata and export steps, a **Legacy recorder
  reconciliation** step appears when at least one `sensor_proxy` entry exists.
  It preselects the stored choice, or the only entry, and never guesses between
  several. Choose **Do not reconcile** to store `null` (`not_linked`).
  Validation refuses entries that are not `sensor_proxy` and out-of-range
  tolerances. If no `sensor_proxy` entry exists, the step is skipped and any
  earlier choice is kept.
- A configured entry that is later removed reads as `invalid`. Spans are then
  skipped, and **Session recorder** reads `not_linked`.

## Sensors

These are diagnostic sensors on the OCPP shadow device:

- **Session recorder**: `legacy_sigen` (or `not_linked`). Attributes:
  `legacy_entry_id`, `link_state`, `ocpp_role`, `legacy_health`,
  `legacy_readiness`, `selector_implemented: false`.
- **OCPP recorder readiness**: the OCPP overall (lowest) level. Attributes:
  `health`, `import_level`/`export_level`, `*_limitations`, `*_sample_age_s`,
  per-direction details, `operator_validation_available: false`.
- **Last OCPP reconciliation**: the explanation of the latest result, `none`, or
  `store_refused`. Attributes: outcome, direction, window, OCPP and legacy kWh
  with bounds, difference kWh/%, allowance, tolerance, last import/export
  explanation, aligned/unresolved/outcome/explanation counts, retained and
  pending counts, lifetime totals.

All three carry `billing_eligible: false` and `settlement_owner: legacy_sigen`.
The same device also has the diagnostic **OCPP session lifecycle** sensor,
which carries the same two attributes. It is described in
[live shadow wiring](ocpp-lifecycle-replay.md#live-shadow-wiring).

## Storage

Results live in a **separate** store, `bsv_settlement.recorder_reconciliation.<shadow entry_id>`,
schema 1. It is separate from the shadow store because:

- it has its own lifecycle, tied to the legacy link;
- a reconciliation fault must not endanger the observation journal;
- adding it to the shadow store would have forced a fourth migration of the
  schema 3 shadow store.

The store keeps the creation time, a per-direction watermark (the last
reconciled span end), at most **50** results (trimming is flagged) and lifetime
totals. It is fully validated on load. Results that claim billing eligibility, a
different settlement owner, an unknown explanation or an inconsistent outcome
are refused, and so are future or unknown schemas (there is no earlier schema).
A refused file is left untouched and never rewritten. Reconciliation is then
disabled (`store_refused`) while shadow observation continues.

## Limitations

- HA arrival times, not charger meter times, align the windows on both sides.
- Precision is bounded by the legacy 10 Wh resolution and ~5 s cadence. Short
  spans are `insufficient_energy` or `legacy_resolution_limited`.
- The change-only legacy history relies on Sigen's ~5 s reporting. A stalled
  legacy feed shows up as `misaligned_window` or as a difference.
- The proxy trims observations older than its previous session, so reconciling
  long after a span closed can find `legacy_missing`.
- There is no operator validation record, selector, switching or financial use.
