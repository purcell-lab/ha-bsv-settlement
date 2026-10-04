# Read-only OCPP export shadow

The export shadow is an optional part of the
[OCPP import shadow](ocpp-import-shadow.md) observer entry. It records how far
the purcell-lab OCPP fork's **derived** export register moved during
conservative observation spans, and grades each span by how wide the fork's
own uncertainty bounds are. It is diagnostic evidence for deciding whether
derived OCPP export could ever be trusted. It is not an export meter, a bill or
a settlement source.

## Safety boundary

- No pricing, net amount, export credit, fee or payment is calculated.
- `billing_eligible` is `false` and `settlement_owner` is `null` on every span,
  summary and sensor. The stored section is refused if a span claims otherwise.
- Which source settles does not change. The Sigenergy `sensor_proxy` recorder
  remains the only accepted payment source; every financial module still
  rejects anything whose mode is not `sensor_proxy`, and the import shadow's
  `export_kwh` and `net_cost_aud` stay `null`.
- Nothing is written to the OCPP integration, no OCPP service or
  `ChangeConfiguration` is called, and the observer only reads HA's entity
  registry and states. The only accepted action remains `refresh`.

## Inputs

All from the purcell-lab OCPP fork, in the **same OCPP config entry and
connector scope** as the bound import measurands (unique ID
`ocpp.<cpid>.<slug>.sensor`, or `ocpp.<cpid>.connN.<slug>.sensor` when the
measurands are connector-scoped):

| Option | Slug | Required | Use |
| --- | --- | --- | --- |
| `export_register_entity` | `energy_active_export_register` | yes | Cumulative derived export kWh and its attributes |
| `flow_direction_entity` | `flow_direction` | yes (with the register) | `import` / `export` / `idle`, for anomaly flags |
| `session_export_entity` | `energy_session_export` | no | Per-transaction derived export, recorded as a cross-check delta |

Register attributes consumed: `source`, `estimated`,
`energy_lower_bound_kwh`, `energy_upper_bound_kwh`, `step_intervals`,
`max_sample_gap_s` and `last_sample_timestamp` (the charger clock, recorded as
the span's first/last source timestamps). `reference_*`/`divergence_*`
diagnostics on the register are not used. The import shadow's lifecycle
sources (transaction ID, connector status) define the active transaction.

Optional `export_reference_entity`: any enabled, registered, **non-OCPP**
cumulative energy sensor with an HA energy unit, for example the existing Sigen
entry's `sensor.garage_sigen_inverter_dc_charger_total_discharging_capacity`
(MWh). The coordinator converts it to kWh with HA's `EnergyConverter`; the pure
ledger only sees kWh. It is never prefilled or guessed.

## Spans

A span opens on the first valid export-register sample after a fresh baseline
while the bound transaction is active (`Charging`, `SuspendedEV`,
`SuspendedEVSE`; suspend/resume with the same transaction does not split). The
opening sample is the baseline, so energy before it is never reconstructed
(`partial_start: true`). A span closes, and the next sample starts a new
baseline, on:

- transaction change or end, or an unknown/inactive connector status;
- a sample gap, or no new sample, longer than `max(180 s, max_sample_gap_s)`
  (HA arrival time and, when available, charger time);
- a register decrease (`register_decrease` anomaly);
- observer restart, reload or options change (`restart_gap`);
- non-numeric, restored, future or unsupported-unit readings
  (Wh/kWh/MWh are converted before differencing);
- out-of-order or conflicting samples, a change between derived and native
  source, or a changed export/import binding.

Energy is never bridged across a closed boundary.

Each span records: `estimate_kwh` (register delta), `lower_kwh`/`upper_kwh`
(bound deltas), `step_intervals` delta, `sample_count`, `max_gap_s`,
first/last charger and HA timestamps, `flow_direction_counts`, labels, flags,
optional reference and session cross-check deltas, and, when closed, `grade`,
`spread` and the `grading` thresholds used.

## Grading

Applied when a span closes, in this order:

| Grade | Rule |
| --- | --- |
| `ungraded` | Bounds missing on any sample (native or upstream build), bounds decreased, or bounds do not bracket the estimate |
| `insufficient_energy` | `estimate_kwh` below the minimum (default **0.1 kWh**) |
| `within_tolerance` | spread ≤ tolerance (default **5 %**) |
| `wide` | spread ≤ wide limit (default **15 %**) |
| `unreliable` | spread > wide limit |

`spread = (upper_kwh − lower_kwh) / estimate_kwh`, stored as a fraction.
Thresholds are options `export_min_energy_kwh` (0.001–100 kWh),
`export_tolerance_pct` and `export_wide_pct` (0 < tolerance < wide ≤ 100).
On the live stepped V2G replay (60 s samples) the estimate is 1.307 kWh, the
bounds 0.94–1.67 kWh bracket the inverter's 1.250 kWh, and the spread of about
56 % grades the span `unreliable`. Steady export grades `within_tolerance`.

## Labels and flags

- `source_label`: `estimated` when the register's `source` is
  `derived_from_negative_import`; otherwise `native_unverified` (the fork stops
  deriving if the charger ever meters export natively; such data has no bounds
  and is not verified, so it is `ungraded`).
- `anomaly_flags`:
  - `export_flow_without_register_advance`: flow `export` at both ends of an
    interval but the register advanced ≤ 1 Wh;
  - `register_advance_during_import`: flow `import` at both ends of an interval
    but the register advanced > 1 Wh (transitions are not flagged because the
    trapezoid legitimately spans them);
  - `register_decrease`.
  The 1 Wh flatness tolerance only affects flags (60 s at −58 W is 0.97 Wh);
  every register delta still counts in `estimate_kwh`.
- `quality_flags`: `bounds_unavailable`, `bounds_decreased`,
  `bounds_do_not_bracket_estimate`, `source_timestamp_unavailable`,
  `native_export_unverified`, `session_export_reset`.
- `provenance_flags`: the import shadow's provenance flags seen during the span
  (`context_defaulted_by_integration`, `transport_unencrypted`, ...).

A flow-direction change is paired with the register sample it arrived with if
its HA time is at most 5 s after that sample; flow states are counted per
sample in `flow_direction_counts`.

Reference: `reference_delta_kwh`, `reference_divergence_kwh`
(estimate − reference) and `reference_divergence_ratio` (divergence /
reference). `reference_status` is `ok`, `unavailable` (missing, invalid or
moved during the span), `reference_decreased` or `not_bound`. Diagnostic only.

## Configuration

Export observation is **disabled until bound**. On an existing OCPP import
shadow entry: **Settings > Devices & services > BSV Settlement > (OCPP import
shadow entry) > Configure**. The first step is the existing provenance metadata
step; submit it (unchanged) to reach **OCPP export shadow**. The export register
and flow direction are prefilled by exact unique ID; select the reference if
wanted, adjust thresholds, and submit. Options are stored as:

- `export_register_entity`, `flow_direction_entity`, `session_export_entity`,
  `export_reference_entity`, `export_min_energy_kwh`, `export_tolerance_pct`,
  `export_wide_pct` (form values);
- `export_binding` (validated entity/unique ID/config entry/device per source),
  `export_reference_binding` (entity ID, unique ID, platform) and
  `export_grading` (Decimal strings) used by the observer.

Validation refuses sensors from another charger, connector or OCPP entry,
disabled sensors, the import measurands, a register without flow direction, an
OCPP or unit-less reference, a reference without an export binding, and
out-of-range thresholds. Clearing all export fields disables observation; the
retained export history stays in the store. Saving reloads the observer, so an
open span ends at a restart gap. If a bound export sensor later changes
identity, the open export span closes and `export_binding_changed` is flagged;
import observation continues.

## Sensors

Both diagnostic, on the observer device:

- **OCPP export shadow last span** (kWh): `estimate_kwh` of the last closed
  span. Attributes: grade, spread, bound deltas, labels, anomaly/quality/
  provenance flags, timestamps, flow counts, reference deltas and divergence,
  thresholds, current span, `billing_eligible: false`,
  `settlement_owner: null`.
- **OCPP export shadow quality**: latest grade, or `not_bound`. Attributes:
  `grade_counts` over retained spans and retention flags.

## Storage

The export section lives in the same store as the import shadow,
`bsv_settlement.ocpp_shadow.<entry_id>`, under the key `export`, with store
schema **3**. One file keeps restart, flush and validation atomic with the
import journal and avoids a second migration path. Schema 2 stores migrate
losslessly (every import record kept, `export: null`, `migrated_from_schema: 2`;
schema 1 stores keep `migrated_from_schema: 1`). The whole store, including the
export section, is validated before HA rewrites the file. Future versions,
malformed sections and spans claiming billing eligibility or a settlement owner
are refused and left on disk untouched. The section retains at most 1,000
journal events and 50 closed spans; trimming is flagged.

## Reconciliation

Closed export spans are also reconciled against the legacy Sigen discharge
counter over the same HA time window (see
[Recorder readiness](recorder-readiness.md#aligned-window-reconciliation)). If
the legacy delta lies within this span's lower/upper bounds, the result is
`ocpp_bounds_contain_legacy`. It counts as within tolerance only when the span's
grade is `within_tolerance`. Derived export is capped at
`fresh_attributable_sample` readiness with the `export_estimated` limitation.

## Limitations

- **Derived, not metered.** The register integrates negative import power
  reported by the charger; it is an estimate with bounds, not a measured export
  register. Native export data, if it ever appears, is labelled
  `native_unverified` and is not graded.
- **60 s sampling floor.** Bounds widen with power steps between samples, so
  stepped V2G profiles grade `wide`/`unreliable` even when the estimate is
  close (+4.5 % against the inverter on the live run, 0.73 % on steady export).
  Grades describe sampling uncertainty, not meter accuracy.
- The reference counter's own resolution and update cadence limit divergence
  accuracy on short spans.
- HA arrival times are not charger times; `last_sample_timestamp` is used only
  where present.
- The first sample after start, reload or a boundary is a baseline only.
