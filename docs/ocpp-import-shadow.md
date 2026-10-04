# Read-only OCPP import shadow adapter

This is an opt-in **observer**, not the OCPP settlement recorder or the source
selector proposed in [issue #61](https://github.com/purcell-lab/ha-bsv-settlement/issues/61).
It leaves the existing Sigenergy recorder, prices, approvals, payment routing and
charger control unchanged. It does not make a second bill for the same energy.
The same entry can optionally observe the fork's derived export register; see
[OCPP export shadow](ocpp-export-shadow.md).

## Setup after an approved deployment

1. Add another **BSV Settlement** integration entry.
2. Select backend `ocpp_import_shadow`.
3. Select the enabled OCPP **Energy Active Import Register**, **Transaction ID**
   and **Status Connector** sensors belonging to the same physical connector.
4. Optionally bind the charger's **Version.OCPP**, **Configuration.Keys** and
   **Boot.Notification** diagnostic sensors in the **Optional OCPP provenance
   metadata** step (see [Provenance](#provenance)). Leave them blank on
   upstream OCPP builds.
5. Inspect the new **OCPP import shadow status** and **Observed import span energy**
   sensors. Optionally add these sensors to a private HA dashboard using native
   entity cards. This PR does not rewrite the settlement dashboard.

The import register must initially be numeric and in Wh, kWh or MWh. Registry
validation requires the three enabled OCPP metrics to share the same device,
integration entry and station/connector unique-ID prefix. Their metric suffixes
are checked against the pinned OCPP layout described in the
[investigation](ocpp-recorder-investigation.md). Unrecognised layouts fail
validation rather than guessing. Source identities are frozen in the observer
entry and checked during observation; renaming or replacing a source requires
review and a new observer configuration. Duplicate observer entries for the
same device/integration are refused by the config flow.

No OCPP service is invoked, and the adapter neither intercepts the OCPP transport
nor changes integration options. It needs only HA's entity registry and states.

## What the numbers mean

- **Observed import span energy:** Decimal-normalised register differences after
  the first fresh, valid, periodic baseline observed by this adapter. It is not
  necessarily the total energy for the native transaction.
- **Native transaction ID:** retained privately in span evidence, with source
  identity bound to the configured integration/device/metric scope. The shadow
  span ID is separate from the native ID and from every settlement session ID.
- **Partial start:** always true. Enabling an observer during a session does not
  reconstruct the missed start or earlier energy from a current register.
- **Observation boundary:** loss/change of transaction identity, an inactive or
  unknown connector state, data gaps, invalid input or a counter decrease can
  end a span. This is not a final OCPP stop or a completed bill.
- **Suspension:** Charging/SuspendedEV/SuspendedEVSE with the same identity do
  not themselves split the observation. Missing/stale data still can.
- **No export or cost:** export energy and net cost remain null, never zero.
  This adapter deliberately does not calculate prices, fees or settlement.
  The optional [export shadow](ocpp-export-shadow.md) reports graded derived
  export estimates separately and never fills these fields.

Freshness uses HA `last_updated`, with a conservative 180-second limit and
five-second future-clock tolerance. These thresholds are observation safeguards,
not validated hardware latency guarantees. A meter gap greater than 180 seconds
excludes the intervening delta and starts a new partial baseline. Exact duplicate
samples do not add energy; conflicting or out-of-order timestamps stop the span.
Non-finite/negative/unsupported-unit readings and restored or non-periodic samples
are rejected. Wh/kWh/MWh conversion happens before subtraction.

The source's original charger measurement timestamp is **unavailable** in this
entity-only adapter. Journal fields explicitly distinguish `ha_updated_at` and
`observed_at`, with `source_timestamp: null`. No negotiated OCPP version is inferred
from measurands; it is only reported from bound fork metadata (below).
Entity values may already have been reduced or rounded by the upstream integration;
this observer cannot recover missing protocol context or verify raw-meter accuracy.

## Provenance

Provenance is descriptive evidence only. It never changes sample acceptance,
billing eligibility or any financial flag, and the observer still sends no OCPP
command (no `ChangeConfiguration`, no service call).

### Fork dependency and upstream degradation

The richer provenance depends on the
[purcell-lab OCPP fork](https://github.com/purcell-lab/ocpp), which publishes:

- a `context_source` attribute on every measurand sensor: `charger` when the
  charger sent a `context`, `defaulted` when the integration filled in
  `Sample.Periodic`;
- charger-level (connector 0) diagnostic sensors with unique IDs
  `ocpp.<cpid>.version_ocpp.sensor`, `ocpp.<cpid>.configuration_keys.sensor` and
  `ocpp.<cpid>.boot_notification.sensor`.

Upstream OCPP builds publish neither. The observer then keeps working exactly as
before: `context_source` is `unknown`, `protocol_version` stays `unverified` and
other provenance fields are `unknown`. Missing, unavailable or malformed metadata
never fails setup or observation.

### Context provenance

The `Sample.Periodic` requirement is unchanged. The live Sigenergy charger sends
no context, so that requirement is met only by the integration's default. Such
samples are still accepted, so the live shadow keeps observing, but are flagged
`context_defaulted_by_integration`. A missing attribute (upstream build) is
flagged `context_source_unknown`; `charger` adds no flag. Each `import_sample`
journal event records `context_source`.

### Binding metadata

New entries offer the **Optional OCPP provenance metadata** step after source
validation. For an entry configured before this release, open **Settings >
Devices & services > BSV Settlement**, choose the OCPP import shadow entry and
select **Configure** (options step **OCPP shadow provenance metadata**, followed
by the optional [export shadow](ocpp-export-shadow.md#configuration) step). When
nothing is bound yet, both forms prefill sensors whose unique ID exactly matches
the bound charger; nothing is matched by entity name. All three fields are optional and can be cleared.

Validation requires each metadata sensor to be an enabled OCPP sensor in the
**same OCPP config entry** with the exact `ocpp.<cpid>.<metric>.sensor` unique ID,
where `ocpp.<cpid>` is the prefix of the bound measurands (a `connN` connector
scope is dropped). Sensors from another charger or another OCPP entry are
refused. They may belong to the charger device rather than the connector device.
The fork's diagnostic sensors may be disabled by default; enable them first.
The three measurand sources and their frozen `source_binding` are unchanged;
metadata is stored separately as `metadata_binding` in the entry options. Saving
options reloads the observer, so an open span ends at a restart gap.

If a bound metadata sensor later changes identity or is disabled, metadata is
ignored and `metadata_binding_changed` is flagged; observation continues. An
entry with no metadata bound shows `metadata_not_bound`.

### Snapshot

When a span opens it records `provenance`:

| Field | Source | Fallback |
| --- | --- | --- |
| `protocol_version` | Version.OCPP state | `unverified` |
| `subprotocol`, `transport` | Version.OCPP attributes | `unknown` |
| `feature_profiles` | `SupportedFeatureProfiles` key, as a list | `unknown` |
| `meter_value_sample_interval_s` | `MeterValueSampleInterval` key | `unknown` |
| `measurands_configurable` | Configuration.Keys attribute | `unknown` |
| `vendor`, `model`, `firmware` | Boot.Notification attributes | `unknown` |
| `meter_identity` | `reported` only if `meter_type`/`meter_serial_number` present | `unavailable` |
| `context_source` | import register `context_source` | `unknown` |

The span's `protocol_version` mirrors the snapshot. Derived `provenance_flags`
include `context_defaulted_by_integration`, `context_source_unknown`,
`protocol_version_unverified`, `transport_unencrypted` (`ws`), `transport_unknown`
and `meter_identity_unavailable`. The live charger is expected to show
`transport_unencrypted`, `meter_identity_unavailable` and
`context_defaulted_by_integration`.

If provenance changes during a span (for example a firmware update), a
`provenance_changed` journal event lists the changed fields with previous and
current values. The span is **not** split, it keeps its opening snapshot, sets
`provenance_changed: true` and accumulates the union of flags seen. The latest
snapshot is also exposed as the `provenance` attribute of both shadow sensors and
its flags are merged into `quality_flags`.

Charger and meter serial numbers are never copied into the snapshot, journal,
store or entity attributes; no serial hash is stored either. Meter identity is
presence-only.

## Storage, restart and safety

The observer owns a separate HA store:
`bsv_settlement.ocpp_shadow.<entry_id>`, schema version 3. It retains at most
1,000 journal events and 50 ended-span summaries plus the current span. Trimming
is flagged. This bounded diagnostic store is not a permanent accounting ledger
and cannot be used as an authoritative bill.

Changed observations schedule a save after one second; unload flushes the store.
A sudden failure can lose the most recent unsaved observations. On load, the
prior span is retained as ended by a restart gap, and a fresh post-start baseline
is required. No energy is bridged across downtime. Unknown schema, malformed
records or changed source bindings are rejected rather than silently reset.

Schema 1 stores migrate to schema 2 and then 3 on first load. Every v1 event and span is
kept unchanged; spans gain `provenance` with unknown fields, `provenance_flags`
including `provenance_not_recorded`, and `provenance_changed: false`. The store
records `migrated_from_schema: 1`. The whole v1 store is validated against the
current source binding before HA rewrites the file, so a rejected store is left
on disk untouched. Future versions are refused without rewriting. Schema 3 adds
the [export shadow](ocpp-export-shadow.md#storage) section (`export: null` for
migrated schema 2 stores); `migrated_from_schema` keeps the oldest schema.

Only the existing `refresh` service is accepted for this entry. Every wallet,
consent, payment and charger action is refused. There is no wallet API object,
no settleable `latest_session` schema and no `sensor_proxy` mode. Existing
payment-source checks and public-tariff selection therefore exclude this observer.
Existing historical payment and consent bindings are not migrated.

## Readiness and reconciliation

[Recorder readiness](recorder-readiness.md) reads this observer's spans and the
legacy recorder. It places OCPP import and export on a readiness ladder and
compares each closed span with the legacy Sigen counter delta over the same HA
time window. Results are diagnostic, are stored separately and never change the
settlement source.

## Validation and next steps

Synthetic fixtures cover activation mid-session, unit changes, invalid readings,
duplicates/conflicts, transaction/lifecycle changes, suspension, stale/future
timestamps, gaps, counter reset, restart separation, bounded retention, context
provenance, metadata snapshots and mid-span provenance changes.
HA-runtime tests cover config/options-flow validation, registry identity
enforcement including cross-charger metadata, separate storage and v1 migration,
sensor summaries without serials, action refusal and setup/unload.

Before calling this native OCPP settlement, complete timestamp-preserving event
capture, actual negotiated protocol/connector evidence, directional export
validation, final-meter reconciliation, independent golden accounts and the
guarded prospective source selector. The current
[sensor-enablement findings](https://github.com/purcell-lab/ha-bsv-settlement/issues/61)
support an import shadow trial, not bidirectional settlement readiness.

Merge, live installation and restart are separate deployment steps. Do not
switch the production recorder or enable a new payment path to test this observer.
