# OCPP lifecycle replay, attribution contract and shadow report

This tranche adds four things for issues
[#8](https://github.com/purcell-lab/ha-bsv-settlement/issues/8) and
[#10](https://github.com/purcell-lab/ha-bsv-settlement/issues/10):

1. Timestamped lifecycle replay fixtures, built from sanitised live Home
   Assistant history and from synthetic edge cases.
2. A deterministic replay harness. It feeds the fixtures through the existing
   [import](ocpp-import-shadow.md) and [export](ocpp-export-shadow.md) shadow
   ledgers and the [recorder reconciliation](recorder-readiness.md), then builds
   native sessions.
3. An explicit session attribution contract, enforced in code and tests.
4. A reproducible shadow validation report generator, with a
   [committed report](qa/ocpp-shadow-report-2026-10-05.md) produced from the live
   fixtures.

Everything here is offline and read-only. It runs no Home Assistant instance and
sends no OCPP or service call.

## Safety boundary

- The OCPP recorder stays shadow-only. The legacy Sigenergy `sensor_proxy`
  recorder remains the only settlement source.
- Every session, span and reconciliation that the replay produces carries
  `billing_eligible: false` and `settlement_owner: legacy_sigen`.
- The new modules `ocpp_lifecycle.py` and `ocpp_replay.py` are pure. They have
  no HA imports and make no service, wallet or charger calls.
- No other integration module imports them, and a test enforces that. The live
  coordinator, sensors and financial modules are therefore unchanged.
- Fixtures were captured with read-only `ha_get_history` calls only. No service,
  button, restart or write was made on the live system.

## Attribution contract

Code: `custom_components/bsv_settlement/ocpp_lifecycle.py`.

| Rule | Enforcement |
| --- | --- |
| Session identity is exactly **(charger, connector, OCPP transactionId, start time)**. | `SessionIdentity` is a frozen dataclass with exactly these fields (`IDENTITY_FIELDS`). It rejects missing, `0` and unavailable transaction IDs. |
| The **idTag is untrusted metadata**, never a vehicle identifier, ownership key or payment key. | `ownership_key()` returns the identity tuple. It raises `AttributionError` if it is passed an idTag, a vehicle hint or anything else. Sessions store only one-way `id_tag_refs`, never the raw tag. A test fails if any other integration module mentions an idTag. |
| idTag changes never split a session, and a shared idTag never merges two sessions. | Tested with the tracker directly and with live back-to-back transactions that carry the same idTag. |
| **Vehicle attribution only comes from external evidence and is never automatic.** | Every session has `vehicle_attribution: unresolved` and `vehicle_attribution_automatic: false`. SoC continuity across a boundary is recorded as `vehicle_evidence` (±2 points, the [#61](https://github.com/purcell-lab/ha-bsv-settlement/issues/61) rule) for an operator to review. |
| A closed transaction is never reopened. | A late or duplicate stop that re-publishes a closed transaction ID only adds `late_or_duplicate_transaction_event` to that session. |
| Energy is never bridged across a session boundary. | A span is attributed only when it has the same transaction ID **and** its first sample falls inside the session window. A register advance after a stop is reported as `late_final_import_kwh` (`late_final_reading_unattributed`) and attributed to no session. |
| Missing, faulted or gapped observation is `None` plus flags, never zero and never merged. | `observed_kwh` is `None` with `no_<direction>_observation`. Energy that the reference counted outside the observed spans is reported separately. |

### Why the idTag cannot identify a vehicle

Live evidence from 4 to 6 October:

- The Sigenergy charger issues a random 16-character idTag for each
  charger-side session. The same car received new idTags with continuous SoC.
- After the car swap at 20:37 on 5 October a new idTag appeared, and SoC jumped
  from 24 % to 70 %.
- On 6 October two consecutive transactions, `1791235916` and `1791236227`, three
  minutes apart, carried the **same** idTag.

The replay keeps those as separate sessions with different ownership keys.

## Lifecycle tracking

`LifecycleTracker` consumes the connector status, transaction ID, idTag and SoC
entity timeline in HA arrival order:

- A new transaction ID opens a session. The start is `start_observed` only when
  the tracker first saw the connector with no transaction. Otherwise the session
  gets `start_not_observed`, for example at window start or after an outage.
- A transaction ID of `0` or empty ends the session (`transaction_cleared`).
  `finishing_at` records the Finishing status separately.
- `unavailable`/`unknown` on status and transaction is an **outage**, such as an
  HA restart or integration reload. It never ends a session. If the same
  transaction returns, the outage is listed in `ha_restarts` and flagged
  `ha_restart_during_session`.
- If a different transaction appears without a stop, the open session ends as
  `superseded_without_stop` with `stop_not_observed`.
- A `Faulted` status during a session is flagged `charger_fault`. Within 120 s
  after a stop it is flagged `charger_fault_at_end`. A session that never
  reached `Charging` is flagged `never_charging`.

## Replay harness

Code: `custom_components/bsv_settlement/ocpp_replay.py`. It mirrors the live
`OCPPShadowCoordinator`:

- The import and export ledgers observe on every change of a watched entity and
  on the 15 s poll. Snapshots have the same shape as the live coordinator's.
- When both connector status and transaction are unavailable, the observer is
  unloaded. When they return, it is rebuilt from the persisted store, exactly as
  after a real HA restart. Its open span ends at `restart_gap`, and the next
  sample is only a baseline.
- Each closed span is reconciled against the Sigen counters with
  `reconcile_span`, as in production.
- `assemble()` attaches spans to sessions. It reports per direction:
  - observed totals, as the sum of spans;
  - the reference delta over the **same span windows**;
  - divergence and allowance at the [documented tolerances](recorder-readiness.md#tolerances)
    (import 1 %, export 5 %, 0.02 kWh floor, 0.1 kWh minimum energy);
  - the reference delta over the whole session, and the energy the reference
    counted outside observed spans.

Reference counters are change-only HA history rows. The last value is treated as
a reading at the window end, because HA history records every change.

## Fixtures

Fixtures live in `tests/fixtures/ocpp_lifecycle/`. Each has `schema`,
`evidence`, `window`, `units`, provenance `metadata` and time-ordered `events`
(`[time, key, state, attributes?]`).

| Fixture | Evidence | Covers |
| --- | --- | --- |
| `live_2026-10-05_v2g_restarts_car_swap` | live, sanitised | import then V2G session `1791165819` over 8.5 h; HA restarts at 14:43 and 19:06; stop at 20:37:21; car swap; back-to-back session `1791196678` |
| `live_2026-10-05_restart_mid_session` | live, sanitised | session already in progress (`start_not_observed`); HA restart at 22:41; stop at 00:11 |
| `live_2026-10-06_restarts_back_to_back_same_idtag` | live, sanitised | two restarts mid-session; an idle restart; a 2-minute session, then a back-to-back session with the **same idTag**; a restart; stop at 09:35 |
| `live_2026-10-06_fault_zero_energy` | live, sanitised | charger-initiated transaction at 11:21:08: Preparing, Finishing, then Faulted, with 0 kWh |
| `live_2026-10-05_fault_early` | live, sanitised | the identical earlier fault at 05:55 |
| `synthetic_start_stop_import` | synthetic | plain 7.2 kW session |
| `synthetic_ha_restart_mid_session` | synthetic | restart with restored states that have no context |
| `synthetic_late_duplicate_stop` | synthetic | closed transaction ID re-published; register advances after the stop; next session |
| `synthetic_back_to_back_export` | synthetic | continuous 2 kW export across a 40 s session boundary |
| `synthetic_fault_zero_energy` | synthetic | Preparing, Finishing, cleared, Faulted |
| `synthetic_meter_gap` | synthetic | 10-minute MeterValues gap while Charging |
| `synthetic_v2g_negative_import` | synthetic (live samples) | 4 October stepped V2G MeterValues as negative `Power.Active.Import`, with a Wh import register. The reference discharge counter is **synthesised** from the inverter's 1.250 kWh change. |

**Live sanitisation** (`scripts/build_ocpp_fixtures.py`):

- idTags become `IDTAG-nn` placeholders per fixture.
- Entity IDs become logical keys.
- Attributes are allow-listed, which drops reference and divergence
  diagnostics, friendly names and serials.
- Timestamps, states and the HA-allocated transaction IDs are kept. The
  transaction IDs are the Unix start time, not secrets.
- No wallet data was read.

To rebuild a live fixture from a fresh read-only capture:

```text
python scripts/build_ocpp_fixtures.py capture.json tests/fixtures/ocpp_lifecycle/<name>.json \
    --name <name> --start <iso> --end <iso> --description "..."
python scripts/build_ocpp_fixtures.py --synthetic tests/fixtures/ocpp_lifecycle
```

The capture is the `ha_get_history` output (`minimal_response: false`, all
changes) for the charger status, transaction ID, idTag, import/export registers,
session export, power, SoC, flow direction and the Sigen DC-charger charge and
discharge counters. A test regenerates the synthetic fixtures byte for byte.

## Shadow validation report

```text
python scripts/ocpp_shadow_report.py tests/fixtures/ocpp_lifecycle/live_*.json \
    --title "OCPP native shadow report: live sessions 5–6 October 2026" \
    --output docs/qa/ocpp-shadow-report-2026-10-05.md
```

The report contains:

- the fixture SHA-256 digests;
- sessions with their identity, end reason and restarts;
- observed and reference energy per direction, with divergence and comparison;
- export grades;
- quality flags;
- idTag references and SoC evidence.

It has no wall-clock, host or git content, so a test checks that the committed
report regenerates exactly.

### Live results (5 and 6 October)

From the [committed report](qa/ocpp-shadow-report-2026-10-05.md):

- 8 native sessions, 7 replayed HA restarts, no overlapping sessions, no
  billable sessions and no automatic vehicle attribution.
- **Import:** 37.290 kWh observed against 37.136 kWh from the reference over the
  same windows (+0.42 %). Both sessions with meaningful import are within the 1 %
  tolerance.
- **Export (derived, estimated):** 62.861 kWh against 62.414 kWh (+0.72 %). All
  five sessions with export are within the 5 % tolerance. Session `1791165819`
  derived 41.337 kWh against the Sigen session counter's 41.28 kWh.
- Both live faults are reported with `None` energy and `charger_fault_at_end`
  and `never_charging` flags, never as 0 kWh.

## Findings from the replay

- **The import register goes stale during discharge.** It does not change while
  the vehicle exports, so its HA `last_updated` stops moving and the import
  shadow closes the span as `invalid_or_stale_meter` after 180 s. This is
  conservative and correct, but it splits a session into many import spans:
  15 for the 8.5 h session.
- **Baselines exclude a little energy.** The first interval after each start,
  restart or stale closure is a baseline. On the long session this is 0.47 kWh
  of import and 0.67 kWh of export, reported as
  `*_reference_energy_outside_observed_spans`.
- **Restored states lack the fork's `source` attribute.** For about one second
  after a restart the export register is labelled `native_unverified`, which
  ends a span with `source_label_changed`. This is the #72 follow-up that treats
  attribute-less restored states as unknown.
- **The live stop sequence is consistent.** Finishing, then about 3 s later the
  transaction cleared, then Available or Faulted. No HA restart lost or changed an
  active transaction ID.

## Remaining work

- Wiring `LifecycleTracker` into the live shadow coordinator with a persisted
  session store and diagnostic sensor. It is staged as offline code only.
- Charger-timestamped events: the fixtures carry HA arrival times. Only the
  export register exposes the charger's `last_sample_timestamp`.
- A live capture of a genuinely late StopTransaction. The late and duplicate
  stop case is synthetic, because no live occurrence was recorded after the
  fork fix.
- The operator validation record that unlocks `validated_directional_accounting`.
  Tariff provenance under #11.
- Latency-informed reserve design under #10.
