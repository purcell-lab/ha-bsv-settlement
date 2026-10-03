# Directional settlement dashboard

The Status tab keeps the charging and settlement summary and adds current charging
(buy) and export/V2G (sell) prices. Sources come from the recorder's configured
price entities, with optional card overrides. Rates must have a valid current
interval and AUD/kWh units; stale readings are withheld.

Session metrics use the exact labels **Energy Imported to EV** and **Energy
Imported from EV**. Each displays kWh and a separate energy-weighted average
AUD/kWh. Charging cost is divided by import energy; export credit is divided by
export energy. Negative rates retain their sign, fees are excluded, and zero or
missing energy has no average.

The existing `payments` path becomes Driver collections. A new `operator-credits`
view contains operator-to-driver payments and credit policy. This preserves old
dashboard links while separating payment directions. Manual review choices and
controls are filtered by direction; completed-charge consent/waiver remains in
Driver collections.

Both views contain a session table with reference, end time, both energy totals
and averages, net AUD, wallet amount, actual fee, fee cap where applicable, and
actual settlement status. Historical meter data that is not retained is shown as
unavailable, not reconstructed. Complete transaction references link to the BSV
mainnet chain-provider record; invalid or missing IDs are never linked.

## Deployment

Merge/install requires separate approval. Regenerate the dashboard configuration
with `frontend.dashboard.redesign` through the established HA MCP dashboard
workflow after installing the card bundles. The migration is idempotent, preserves
user cards and additional views, and refuses ambiguous/partial layouts. Refresh
the dashboard and driver page after deployment.

No payment, approval, recipient, fee policy, signed transaction or waiver changes
as a result of the dashboard migration. Multi-session consent is a separate change.

## QA inventory

- Desktop and mobile Status: live buy/sell prices distinct from per-direction
  session averages; exact energy labels; no viewport overflow.
- Direction tabs: driver-only versus operator-only rows/actions; table scroll
  remains inside the card; preserved completed-charge closure controls.
- Pending and confirmed states: amounts and actual fees distinct from fee caps;
  provider hyperlinks appear only for well-formed transaction IDs.
- Empty/missing-data and stale-rate cases: no invented zero or price.
- Dark/light themes and keyboard focus: readable data and controls.
- Existing private configuration and extra cards survive migration unchanged.
# Provisional amount units

The Status card shows provisional driver credits and charges in sat. AUD remains
a secondary accounting reference. Use the session's fixed conversion where
available, otherwise label the configured sensor rate as indicative. Missing
conversion data is unavailable, never zero. Round positive sat amounts half up;
exclude network fees and retain the actual settlement status separately.
