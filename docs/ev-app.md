# EV app: HA engine, new front door

`apps/ev-app/` is a separate driver app built with the official
`create-bsv-app@1.1.2` framework (React + Express, `@bsv/auth` BRC-103 wallet
login and signed requests). The Home Assistant integration stays the single
source of financial truth, and the app is only a front door to it. It isn't part
of the HACS payload: HACS installs only `custom_components/bsv_settlement`, and
the Python gates don't look at `apps/`.

Setup, environment variables, API and threat notes are in
[apps/ev-app/README.md](../apps/ev-app/README.md).

## Milestone plan

| Milestone | Scope | Status |
|---|---|---|
| **M1: read-only** | Public station status read from allowlisted HA sensors through `GET /api/states/<id>` only, using a dedicated non-admin HA token. After BRC-103 wallet sign-in, a driver sees only their own credit rows from the operator wallet status sensor. No payment, signing, broadcast, collection, credit, waiver, recovery or charger actions. | This change |
| **M2: driver flows behind HA services (gated)** | Port the driver weekly approval and collection flows. The app forwards signed requests to new, narrowly scoped HA services, and HA verifies and authorises every financial action. Needs a scoped driver-history service (per identity, beyond the sensor's display window), a shared nonce and session store, and owner approval before enabling. | Not started; gated |
| **M3: wallet-relay pairing (gated)** | Evaluate replacing the custom browser QR pairing with `@bsv/wallet-relay`. Gated on BSV Browser acceptance evidence comparable to [bsv-browser-acceptance.md](bsv-browser-acceptance.md). | Not started; gated |

## M1 guarantees

- The HA client can only `GET /api/states/<entity_id>` for configured entities.
  Redirects are refused. Every read has a timeout, a byte cap, strict JSON
  parsing and a shape check. HTTPS is required unless `HA_ALLOW_INSECURE_LAN=1`
  is set for a LAN host.
- The HA token is never sent to the browser, logged, or put in an error message.
- Unknown or unavailable values are `null` with a reason, never `0`. Provisional
  cost is labelled provisional and is not a bill. The satoshi rate is labelled
  "demonstration rate, not market FX". OCPP values are shadow only.
- Credit rows are filtered to the verified identity key. Rows with no verifiable
  identity are excluded.
- Tests run against a fake HA server only and cover each of these points.
