# Read-only OCPP import shadow adapter

This is an opt-in **observer**, not the OCPP settlement recorder or the source
selector proposed in [issue #61](https://github.com/purcell-lab/ha-bsv-settlement/issues/61).
It leaves the existing Sigenergy recorder, prices, approvals, payment routing and
charger control unchanged. It does not make a second bill for the same energy.

## Setup after an approved deployment

1. Add another **BSV Settlement** integration entry.
2. Select backend `ocpp_import_shadow`.
3. Select the enabled OCPP **Energy Active Import Register**, **Transaction ID**
   and **Status Connector** sensors belonging to the same physical connector.
4. Inspect the new **OCPP import shadow status** and **Observed import span energy**
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

Freshness uses HA `last_updated`, with a conservative 180-second limit and
five-second future-clock tolerance. These thresholds are observation safeguards,
not validated hardware latency guarantees. A meter gap greater than 180 seconds
excludes the intervening delta and starts a new partial baseline. Exact duplicate
samples do not add energy; conflicting or out-of-order timestamps stop the span.
Non-finite/negative/unsupported-unit readings and restored or non-periodic samples
are rejected. Wh/kWh/MWh conversion happens before subtraction.

The source's original charger measurement timestamp is **unavailable** in this
entity-only adapter. Journal fields explicitly distinguish `ha_updated_at` and
`observed_at`, with `source_timestamp: null`. No negotiated OCPP version is inferred.
Entity values may already have been reduced or rounded by the upstream integration;
this observer cannot recover missing protocol context or verify raw-meter accuracy.

## Storage, restart and safety

The observer owns a separate HA store:
`bsv_settlement.ocpp_shadow.<entry_id>`, schema version 1. It retains at most
1,000 journal events and 50 ended-span summaries plus the current span. Trimming
is flagged. This bounded diagnostic store is not a permanent accounting ledger
and cannot be used as an authoritative bill.

Changed observations schedule a save after one second; unload flushes the store.
A sudden failure can lose the most recent unsaved observations. On load, the
prior span is retained as ended by a restart gap, and a fresh post-start baseline
is required. No energy is bridged across downtime. Unknown schema, malformed
records or changed source bindings are rejected rather than silently reset.

Only the existing `refresh` service is accepted for this entry. Every wallet,
consent, payment and charger action is refused. There is no wallet API object,
no settleable `latest_session` schema and no `sensor_proxy` mode. Existing
payment-source checks and public-tariff selection therefore exclude this observer.
Existing historical payment and consent bindings are not migrated.

## Validation and next steps

Synthetic fixtures cover activation mid-session, unit changes, invalid readings,
duplicates/conflicts, transaction/lifecycle changes, suspension, stale/future
timestamps, gaps, counter reset, restart separation and bounded retention.
HA-runtime tests cover config-flow validation, registry identity enforcement,
separate storage, sensor summaries, action refusal and setup/unload.

Before calling this native OCPP settlement, complete timestamp-preserving event
capture, actual negotiated protocol/connector evidence, directional export
validation, final-meter reconciliation, independent golden accounts and the
guarded prospective source selector. The current
[sensor-enablement findings](https://github.com/purcell-lab/ha-bsv-settlement/issues/61)
support an import shadow trial, not bidirectional settlement readiness.

Merge, live installation and restart are separate deployment steps. Do not
switch the production recorder or enable a new payment path to test this observer.
