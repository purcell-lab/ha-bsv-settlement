# OCPP recorder investigation and operator source selection

Read-only investigation dated 4 October 2026. This document supports
[issue #61](https://github.com/purcell-lab/ha-bsv-settlement/issues/61) and the
[native OCPP adapter plan](https://github.com/purcell-lab/ha-bsv-settlement/issues/8).
It proposes implementation, not a change to the live recorder.

## Bottom line

The installed OCPP integration is connected and exposes useful session data.
It is a viable adapter target, but the inspected entities alone do not yet
establish complete, timestamped bidirectional settlement evidence. Keep the
working Sigenergy recorder selected while implementing a source-neutral adapter
and read-only OCPP shadow comparison.

The operator should select **Legacy Sigenergy** or **OCPP** for future sessions.
That selection must not change charger control, redirect a historical payment,
extend a driver's consent or create a second account for the same energy.

## Evidence and limits

The live inspection used Home Assistant MCP integration/HACS metadata,
entity/state reads, bounded recorder history, service discovery and read-only
Python source reads. No configuration service, charger command, restart,
wallet operation or payment was requested. Public evidence is summarised in
[issue #61](https://github.com/purcell-lab/ha-bsv-settlement/issues/61);
site identifiers, serials, id tags, native transaction references, endpoint
addresses and raw logs are deliberately excluded.

| Item | Observed result | Interpretation |
|---|---|---|
| HACS repository | `lbbrhzn/ocpp`, installed `v0.12.0` | Use the installed tag/commit, not assumptions based on an older release |
| Active configuration | One loaded central system and one ignored discovery entry | The ignored entry is not a second operating server |
| Installed source comparison | Seven inspected Python files exactly match upstream commit `848407c11ff659ce59779a99ce69984bbb0e3ce1` | Strong provenance for those files, not a claim to have compared the entire installation |
| Charger | SIGEN EVDC 25 7.5S2, firmware V100R001C21SPC117, one connector | Identifies the tested hardware/software family without publishing a serial |
| Transport/session observations | Recent latency updates, native transaction ID, Preparing/Charging/Finishing history, session duration and energy | Useful native integration telemetry; not proof of every protocol edge case |
| Connector versus global status | Connector reported Charging while charger-level status was unknown | Bind recorder to the physical connector |
| Energy units | Session energy in kWh; meter-start display scaled from kWh to MWh | Normalise each reading using its own unit; a unit change is not a physical reset |
| Directional registers | No separately exposed import/export cumulative measurand entities in the inspected inventory | Do not assume unsupported hardware, but do not fabricate export or bidirectional readiness |
| Feature/control state | Features reported CORE; current-limit controls were unavailable | No verified smart-charging or V2G-control capability |
| Active protocol | Negotiated version not independently recovered from the bounded read surfaces | Record as unverified until handshake/runtime evidence is available |

The [`v0.12.0` tag](https://github.com/lbbrhzn/ocpp/tree/848407c11ff659ce59779a99ce69984bbb0e3ce1)
contains a [manifest still declaring `0.11.2`](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/manifest.json).
Do not overwrite the HACS finding with the manifest string or infer that an
older implementation is running. The exact installed-source comparison covered
`__init__.py`, `api.py`, `chargepoint.py`, `const.py`, `ocppv16.py`,
`ocppv201.py` and `sensor.py`.

The installed [configuration constants](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/const.py#L61-L79)
offer 1.6, 2.0.1 and 2.1; this is implementation support, not evidence that the
connected charger negotiated every version or provides V2G on any of them.
The default meter interval is 60 seconds, but that default is not a verified
live configuration value.

## Session signals and adapter constraints

| Native metric or event | Proposed use | Required guard |
|---|---|---|
| Connector status | Availability and lifecycle context | Charging, suspension and Finishing alone cannot prove final accounting closure |
| Transaction ID | Protocol transaction binding | Scope to integration, station, EVSE/connector and generation; do not treat an ID as a timestamp |
| Session energy | Shadow comparator and possibly an explicitly import-only session total | Verify cumulative-versus-session semantics and whether import/export is actually represented |
| Meter start | Baseline candidate | Preserve unit, provenance, source timestamp and counter semantics |
| Session duration | Operator display/cross-check | Not the primary start timestamp or billing interval clock |
| Stop reason | End-of-session context | Require a matched transaction and final-reading/reconciliation policy |
| `Energy.Active.Import.Register` | Preferred cumulative EV import evidence when exposed | Check units, phase/location, connector and transaction attribution |
| `Energy.Active.Export.Register` | Preferred independent EV export evidence when exposed | Missing means unavailable, not zero; never derive it from household power |
| Heartbeat/latency | Connection health | Fresh connection does not prove fresh meter evidence |

### Protocol identity is not always charger-issued

For 1.6, the inspected
[StartTransaction handler](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv16.py#L1804-L1855)
allocates the transaction ID in the central system and returns it to the
charger. Use **native OCPP transaction ID**, not a universal promise of a
charger-generated ID. The settlement session UUID remains separate and stable.
The [2.x implementation](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv201.py)
has its own TransactionEvent and EVSE/connector mapping, buffering and ordering
logic; use version-specific fixtures rather than one generic lifecycle parser.

### Preserve meter timestamps, not just state-change arrival times

Home Assistant entity history shows delivered state changes, not necessarily
the original per-sample meter timestamps. The inspected
[1.6 meter handler](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv16.py#L1517-L1712)
reduces protocol samples into metrics; its session start handling uses local
time. A settlement adapter cannot assume the entity's `last_updated` is the
charger's measurement time or that the derived session duration supplies it.

Use a supported, timestamp-preserving upstream event/adapter boundary where
possible. Retain raw event identity, timestamp, context, phase, location and
unit in a bounded private journal before normalisation. Do not build a recorder
by parsing debug logs or intercepting the OCPP websocket with another proxy.

### Do not reuse private hooks as an undocumented public event bus

The inspected
[chargepoint implementation](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/chargepoint.py#L868-L908)
has optional start/end callbacks through `session_controller`. Sensor updates
use a dispatcher, but neither constitutes a documented, durable settlement
stream containing all timestamped directional samples.

Prefer an upstream observer/event contract that does not replace an existing
controller or affect protocol replies. If a pinned internal adapter is needed
for the demonstration, isolate version checks and fail closed on incompatibility;
do not monkey-patch the protocol handler in production.

### Units, counter semantics and invalid samples

Normalize Wh, kWh and MWh into a Decimal-based canonical energy unit before
calculating a delta. Retain the original value/unit alongside the normalized
value. Do not round a cumulative register before deriving interval energy.
The observed kWh-to-MWh change represented the same energy, not a 99.9% drop.

The upstream
[meter processing](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/chargepoint.py#L1340-L1450)
handles both lifetime-register and session-energy behaviour. The 1.6 handler
can coerce malformed numeric samples to zero. Settlement must retain quality
evidence and reject invalid data rather than relying on a zero-valued metric
as proof of no energy.

OCPP final readings can arrive around or after the status transition. Preserve
the transaction through a bounded finalisation/reconciliation phase. Unknown,
disconnected or ambiguous evidence remains unresolved; do not carry the legacy
Sigenergy **Occupied** rule into all OCPP protocol versions.

## Proposed recorder interface

Keep the tariff engine, frozen financial accounts and wallet state machines
independent of both source adapters.

```text
RecorderAdapter
  identity: recorder_id, source_kind, adapter_version, physical_connector_key
  capabilities: lifecycle, import_register, export_register, source_timestamps
  health: ready | shadow_only | stale | unavailable | incompatible
  observe: normalized lifecycle and meter events
  history: immutable recorded sessions
  reconcile: original transaction and final-meter evidence

RecordedSession
  session_uuid
  source_kind: legacy_sigen | ocpp
  recorder_id, adapter_version, selection_generation
  integration_entry_id, station_key, evse_id, connector_id
  protocol_version, native_transaction_id
  opened_at, ended_at, finalisation_state
  source_timestamp, observed_at, event_identity, sequence
  original_value, original_unit, normalized_energy
  import_intervals, export_intervals, tariff_references
  quality_flags, provenance, physical_activity_correlation
  immutable_approval_binding, settlement_owner_key
```

These are proposed logical fields, not a committed public API. Migration must
retain legacy record IDs, quotes, signed terms and all existing payment evidence.
Create a new adapter kind; do not rename `sensor_proxy` in old stored records.

## Operator selector and safe switching

Place **Session recorder** on the operator Charging and settlement tab.
Show Legacy Sigenergy / OCPP, the selected charger/connector, readiness,
directional capability, last valid sample, shadow comparison and active or
pending selection. Readiness is not a green payment-authority indicator.

1. **Default:** preserve Legacy Sigenergy for existing installations.
2. **Shadow:** allow OCPP observation without acquiring payment ownership.
3. **Request:** show the prospective source and any changed scope or limitations.
4. **Boundary:** require reconciled idle state on the same physical connector.
   Active, finalising or indeterminate sessions block immediate switching;
   a queued switch can be inspected/cancelled by the operator.
5. **Commit:** atomically persist source choice, effective time, selection
   generation, administrator and reason. Restart must not activate half a switch.
6. **Future ownership:** only the selected source creates settleable accounts.
   Shadow records never trigger payment. Correlate the same physical charging
   activity explicitly because the two sources may split it differently.
7. **Historical ownership:** every open or closed session retains its original
   adapter, meter evidence, recipient, authority and payment/recovery path.
8. **Failure/rollback:** no silent fallback or historical replay. If OCPP fails,
   show unresolved status and permit only an explicitly reviewed future switch.

An import-only OCPP mode is possible only as a deliberately scoped pilot once
its import evidence is validated. If export is possible but unmeasured, the
financial result is incomplete; it must not be represented as a valid
bidirectional net account.

## Integration impact

The current settlement code is explicitly coupled to `sensor_proxy` and
`proxy_config_entry_id`. This is not a dropdown-only change.

| Area | Required change |
|---|---|
| Config/setup | Add an OCPP recorder backend and a source-selection record; keep old entries compatible |
| Session accounting | Common source interface, native transaction mapping, unit/timestamp quality and immutable finalisation |
| Budgets/weekly approvals | Freeze the source/connector scope; require fresh approval if the existing signed terms do not cover the new source |
| Ongoing operator-credit policy | Preserve historical routes and require explicit policy scope for the new recorder |
| Collections/reviews/recovery | Resolve the recorder stored in the original record, not whichever one is currently selected |
| Operator tables/driver history | Show source labels while retaining original native/proxy transaction references |
| Pricing and public rate cards | Resolve selected-recorder tariff configuration; never choose an arbitrary site when two adapters exist |
| Restore | Persist selection atomically; prevent a stale selection from creating a duplicate physical-activity settlement |

The pending
[sign-in rate work in PR #59](https://github.com/purcell-lab/ha-bsv-settlement/pull/59)
deliberately refuses ambiguous multiple-proxy pricing. Replace that assumption
with explicit source selection as part of this design, not with a “first
available recorder” shortcut.

## Staged implementation and exit evidence

| Stage | Deliverable | Exit gate |
|---|---|---|
| Contract/fixtures | Neutral schema, migration rules, synthetic native/legacy event fixtures | Units, identity, finality and consent scope agreed |
| Read-only adapter | Lifecycle and timestamped directional metering | Version/connector verified; unsupported export stays unavailable |
| Shadow comparison | Independent import/final meter and tariff reconciliation | Explain every material difference; no duplicate account |
| Selector | Prospective atomic selection and visible guards | Active/unknown switch refusal, restart and rollback tests pass |
| Financial integration | Original-source recovery, fresh scope where needed | Old pending credits/collections unchanged; one owner per physical activity |
| Live trial | Separately approved bounded installation and observation | CI/review, explicit restart authority and clearly scoped acceptance |

Priority fixtures include Wh/kWh/MWh scaling, missing export, meter reset,
malformed values, delayed final samples, duplicate/out-of-order events, reconnect,
restart during finalisation, native transaction reuse, multi-connector ambiguity,
source change during charging, queued-switch cancellation and simultaneous
legacy/OCPP observations of the same activity.

No release tag, charger command, funded transaction, production deployment or
source switch follows from this document. It advances investigation in #8;
native bidirectional accounting and independent acceptance remain open.
