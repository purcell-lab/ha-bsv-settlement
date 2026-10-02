# Live sensor session proxy

The `sensor_proxy` backend is a separate, read-only configuration entry. It
observes charging sessions and estimates energy costs without creating a
wallet, signing transactions, requesting payments or controlling a charger.
It does not implement native OCPP messaging, driver authorisation or a budget
gate.

## Configuration

Add the BSV Settlement integration, select `sensor_proxy`, and choose five
existing, distinct entities:

| Input | Required semantics |
|---|---|
| Charging energy | Cumulative charger-to-EV register in MWh |
| Discharging energy | Cumulative EV-to-charger register in MWh |
| Running state | Sigen-style `Idle`, `Occupied`, preparation, `Charging`, `Discharging`, `Ended` |
| Import price | AUD/kWh, reported as `$/kWh` |
| Feed-in price | Signed export revenue in AUD/kWh, reported as `$/kWh` |

Price attributes must include ISO `start_time`, `end_time` and an `estimate`
flag. The operator must check that the energy and state entities represent the
same physical charger. The configuration flow validates entity existence,
distinctness and units; it does not certify metering or prove device identity.

No source entity names, wallet addresses or private installation identifiers
are hardcoded into the published component. It requires HA recorder history
for startup/restart gap recovery; ensure all five source sensors are recorded.

## Session and price rules

- A candidate opens on an occupied, preparation or active state, but appears
  as an energy session only after charging/discharging activity.
- A return to `Occupied` after charging or discharging closes the activity
  session at that transition. `Ended` and `Idle` also close it. Initial
  `Occupied` or preparation states without energy activity do not create an
  empty completed session.
- Direct charging/discharging direction changes remain within the same session.
  After an `Occupied` close, the next energy activity is a separate session,
  even if the vehicle stays plugged in. Its candidate opening timestamp is the
  `Occupied` boundary; it appears as a session only once energy activity starts.
  The previous wallet approval is not reused for that new session.
- UUID transaction IDs derive deterministically from the configured state
  entity and observed opening timestamp. They are explicitly proxy-generated,
  not charger-issued OCPP transaction IDs. Renaming the source entity or
  changing inferred boundaries can change the derived identity.
- A counter decrease is quarantined. Recovery from zero is never counted as
  a fresh lifetime total. If a register has not recovered, its energy and the
  net cost remain unknown.
- Each positive cumulative increment is apportioned by matching
  charging/discharging-state duration between observations, then split at price
  boundaries. If no state matches, a time-prorated fallback is flagged.
- Effective tariff periods determine prices, not arrival times. The latest
  non-estimated value for each period is preferred; provisional rates remain
  flagged. Forecast arrays are not used.
- Net cost is import kWh times import price, minus export kWh times signed
  feed-in price. Negative feed-in rates are charges. Round only the final net
  to cents.
- Missing tariffs, incomplete starts, unavailable sources and unrecovered
  counters withhold the complete net cost. Previously detected anomalies
  remain in quality flags even if readings recover.

Every result remains a provisional DC-register valuation, not a final
retailer invoice or a settlement-ready account. There is no added standing
charge, loss factor, tax adjustment, exchange rate or wallet fee.

## Automatic updates and persistence

State-change listeners collect compact observations, including attribute-only
price updates. A coordinator recalculates the latest and previous session every
15 seconds while the entity platform is active. A rate may initially be
estimated or missing while its source catches up; the displayed result revises
as the rate arrives.

On first setup, the recorder backfills up to 24 hours. After reload/restart,
it merges a five-minute overlap before the last checkpoint, up to 24 hours of
recovery. Exact observation timestamps are deduplicated. A longer downtime is
flagged and costs are held for review rather than silently bridged.

Private HA storage checkpoints every five minutes, on a new latest session and
on integration unload. This reduces repeated large storage writes; recorder
history is required to recover observations since the previous checkpoint.
The latest two sessions retain source observations for repricing, and up to 50
older summary records are retained. Their amounts are not immutable commercial
invoices. This is not an unlimited audit archive or a substitute for recorder
backup and retention management.

The `Occupied` boundary rule is applied when retained observations are replayed
after activation. A previously open record keeps its original opening-derived
ID but closes at its first observed `Occupied` return after energy flow. Later
activity gets a different ID. Already signed payment records are never replaced
or automatically paid again; changed frozen accounts fail their existing checks.

At most 60,000 observations per input may be collected. Reaching that limit
fails closed with a visible issue; it does not silently discard energy and
continue billing. A long uninterrupted session may require an explicit
retention redesign before this limit is reached.

## Entities and dashboard

The recorder exposes status, proxy transaction ID, session import/export kWh
and provisional AUD cost. The status entity carries compact `latest_session`
and `previous_session` attributes, source mappings, quality issues and the
last calculation timestamp. Full raw history is not stored in entity
attributes.

Native Markdown dashboard cards can reference those attributes and update
automatically. Label the ID origin, provisional costs and stale/unavailable
states. Do not connect these sensors directly to a wallet broadcast action.
The generic `refresh` action may refresh this backend; all wallet/session
mutation actions are rejected for it.

## Validation boundary

Tests cover stable identity, initial and repeated occupied states, occupied
session closure, resumed-session separation, direction changes, negative feed-in
prices, effective-period selection, final-versus-estimated rates, missing
tariffs, counter resets, unavailable sources, storage/reload and read-only
service guards. A private historical replay matched the previously generated
session IDs and rounded estimates; private histories are not committed.

Install through HACS. Existing loaded component code needs an authorised HA
restart to activate this development change; no new release tag is implied.
The offline wallet, mainnet wallet and synthetic mock remain separate entries.
