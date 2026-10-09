# EV app (milestone 1, read-only)

A driver-facing front door for the BSV EV-charging settlement, built with the
official [`create-bsv-app`](https://www.npmjs.com/package/create-bsv-app) framework
(`create-bsv-app@1.1.2`, custom starter, React + Express, capabilities
`wallet-login,signed-requests`; provenance in [`bsv-scaffold.json`](bsv-scaffold.json)).

**HA engine, new front door.** The Home Assistant integration
(`custom_components/bsv_settlement`) remains the single source of financial
truth. This app holds no financial authority: in milestone 1 it can only read a
fixed allowlist of Home Assistant sensor states. It has no payment, signing,
broadcast, collection, credit, waiver, recovery or charger actions.

See [docs/ev-app.md](../../docs/ev-app.md) for the milestone plan.

## Architecture

```text
 Browser (React client, client/)                     Express server (server/)                 Home Assistant
 ───────────────────────────────                     ────────────────────────                 ──────────────
 Station card ── GET /api/station ─────────────────▶ rate limit ▶ readStation ─┐
                                                                               │  GET /api/states/<allowlisted id>
 Sign in with wallet                                                           ├─────────────────────────────────▶ REST API
   wallet.createSignature (BRC-103 proof, action "login")                      │  Bearer HA_TOKEN (server-side only)
   ── POST /api/login ─────────────────────────────▶ verify proof + nonce ──▶ session token (memory, 15 min)
 My credits ── GET /api/me/credits ────────────────▶ session/signed proof ▶ wallet status sensor
            (Authorization: Bearer <session>        ▶ filter rows to the verified identity key
             or X-BSV-Auth-Proof: <signed request>) ▶ minimal projection
```

- `server/src/ha/config.ts`: environment-only configuration and entity allowlist.
- `server/src/ha/client.ts`: the only HA call, `GET /api/states/<entity_id>`.
- `server/src/ha/station.ts`: public station view. Unknown values stay `null` with a reason.
- `server/src/ha/credits.ts`: per-identity credit filter and projection.
- `server/src/app.ts`: routes, CORS, token-bucket rate limit, auth.
- `server/src/bsv/*`: generated BRC-103 helpers. `loginRoute.ts` also mints a read-only session.
- `client/src/ev/*`: driver page, API calls and the unknown-never-zero formatter.
- `client/src/bsv/*`: generated wallet connect/login helpers (desktop wallet or `@bsv/wallet-relay` QR).

## Home Assistant setup: dedicated non-admin user

1. In Home Assistant, go to **Settings → People → Users** and add a user, for
   example `ev-app-reader`. Leave **Administrator** switched off. Don't reuse a
   personal or admin account.
2. Sign in as that user, open **Profile → Security → Long-lived access tokens**,
   create a token, and store it only in the server environment (`HA_TOKEN`).
3. Revoke the token from that profile page if it might have leaked, or when you
   retire the app.

Home Assistant tokens aren't scoped per entity. A non-admin token can still read
every entity state, and it can call non-admin services. This app's own
allowlist and GET-only client are what keep it read-only. Using a non-admin
user limits the damage if the token leaks: it can't change configuration, add
users or install integrations. Treat the token as a secret.

## Environment

Server (`server/`):

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `HA_URL` | yes | - | Home Assistant base URL. Must be `https://`. |
| `HA_TOKEN` | yes | - | Long-lived token of the dedicated **non-admin** HA user. Never sent to the browser or logged. |
| `HA_ALLOW_INSECURE_LAN` | no | unset | `1` permits an `http://` URL, but only to a LAN host (RFC 1918, loopback, link-local, ULA, single-label, `.local`, `.lan`, `.home.arpa`, `.internal`). |
| `HA_WALLET_ENTRY_ID` | no | unset | Reserved for M2 (targeting a specific wallet config entry). Format checked only. |
| `HA_TIMEOUT_MS` | no | `5000` | Whole-response deadline per HA read. |
| `HA_MAX_RESPONSE_BYTES` | no | `524288` | Response byte cap per HA read. The wallet status sensor is about 50 KB on the live system. |
| `HA_ENTITY_*` | no | live entity ids | Override entity ids: `RECORDER_STATUS`, `PROVISIONAL_COST`, `IMPORT_ENERGY`, `EXPORT_ENERGY`, `PROXY_TRANSACTION_ID`, `SATOSHIS_PER_AUD`, `IMPORT_PRICE`, `EXPORT_PRICE`, `WALLET_STATUS`, `OCPP_SHADOW_LIFECYCLE`. |
| `SERVER_PRIVATE_KEY` | prod | random (dev) | Generated: the server's verify-only identity key. It holds no funds. |
| `CLIENT_ORIGIN` | prod | `http://localhost:5173` | Generated: the single CORS origin. |
| `PORT` | no | `3000` | Generated. |
| `TRUST_PROXY` | no | unset | Number of reverse-proxy hops to trust for the client IP used by the rate limit. |

Default entity allowlist:
`sensor.sigen_charging_sessions_recorder_status`,
`sensor.sigen_charging_sessions_provisional_session_cost`,
`sensor.sigen_charging_sessions_session_import_energy`,
`sensor.sigen_charging_sessions_session_export_energy`,
`sensor.sigen_charging_sessions_proxy_transaction_id`,
`sensor.bsv_satoshis_per_aud`,
`sensor.amber_express_amber_general_price`,
`sensor.amber_express_amber_feed_in_price`,
`sensor.bsv_operator_wallet_mainnet_operator_wallet_status`,
`sensor.ocpp_import_shadow_ocpp_session_lifecycle`.

Client (`client/`): `VITE_API_URL` (required for production builds, HTTPS) and
`VITE_BSV_NETWORK` (generated, default `test`).

## Run, develop and test

```sh
cd apps/ev-app/server && npm ci
cd ../client && npm ci
cd ..
HA_URL=https://ha.example.net HA_TOKEN=... npm run dev   # starts server :3000 and client :5173

# tests (no live Home Assistant; a fake HA server is used)
cd server && npm test && npm run typecheck && npm run build
cd ../client && npm test && npm run build
```

Node 22 or later is required.

## API

- `GET /api/station` (public). Returns the recorder state, the current session's
  transaction id and import/export energy, and the provisional cost (labelled as
  provisional, not a bill). It also returns import/export prices, satoshis per AUD
  (a **demonstration rate, not market FX**) and the OCPP shadow lifecycle (shadow
  only). Each value is `{value, unit, reason, last_updated}`. If HA reports a value
  as unknown or unavailable, or it can't be read, `value` is `null` and `reason`
  says why. It is never `0`.
- `POST /api/login`. Generated BRC-103 login. In ev-app it returns
  `{identityKey, sessionToken, expiresAt}`.
- `GET /api/me/credits`. Requires `Authorization: Bearer <sessionToken>` or
  `X-BSV-Auth-Proof: base64(JSON proof)` (a signed request, action `me.credits`,
  no body). Returns `{identity_key, credits, reason, note}`. `credits` contains only
  rows belonging to the verified identity, each with `source, session_id, state,
  amount_sats, fee_sats, net_amount_aud, txid, confirmations, created_at,
  wallet_receipt_status`. If the wallet sensor is unknown or unreadable, `credits`
  is `null` rather than an empty list.
- Generated: `GET /health`, `GET /api/identity`, and the `@bsv/wallet-relay` routes
  (`/api/session`, `/api/request/:id`, `/ws`) for mobile wallet pairing.

### How credit rows are attributed

- `ongoing_credit.sessions` rows are included only when `driver_identity` exactly
  equals the verified identity key.
- `automatic_credit.payments` rows have no `driver_identity`. A row is included
  only when its `budget_id` matches the `receiving_budget_id` of one of the
  identity's own ongoing rows. If the row has no `budget_id`, its
  `recipient_address` must match one of those rows instead. The link must not
  also be claimed by another identity's row.
- Anything else is excluded: rows without an identity, malformed identities,
  unlinked or ambiguous rows. Recipient addresses, budget ids and other
  attributes are never returned.
- The sensor only shows a display window: unresolved rows plus the newest
  resolved ones. Older resolved credits can be missing. Milestone 2 needs a
  scoped HA driver-history service to fix this.

## Threat notes

- **Token stays server-side.** `HA_TOKEN` exists only in the server process
  environment and in the `Authorization` header of the HA read. It is never in
  responses, logs or error messages. HA read failures are logged as
  `code + entity id` only. Tests assert this with a fake HA server.
- **Read-only allowlist.** The HA client can only issue
  `GET <HA_URL>/api/states/<entity_id>` for configured entity ids. There is no
  code path to `/api/services`, the websocket API, or any write. Redirects are
  refused rather than followed. Every read has a whole-response timeout, a byte
  cap, strict UTF-8 JSON parsing and a shape check (`entity_id` must match the
  request). HTTPS is required except for an explicit LAN opt-in.
- **No financial authority.** The app can't sign, approve, pay, broadcast,
  collect, credit, waive, recover or control the charger. The only wallet
  signature it asks for is the BRC-103 login or signed-request proof. Amounts
  are labelled provisional, and HA stays authoritative.
- **BRC-103 nonce store and sessions are single-process.** The generated
  `nonceStore.ts` and `http/sessions.ts` keep their state in memory, so replay
  protection and sessions only hold within one long-lived process. A restart
  signs everyone out. Before running more than one instance, or serverless,
  replace both with a shared atomic store such as Redis or a database with a
  unique index.
- **Rate limit.** An in-memory token bucket per client IP (burst 30, refill
  1/s) covers every HTTP route, including the relay's REST routes. Websocket
  upgrades on `/ws` are not covered. Behind a reverse proxy, set `TRUST_PROXY`,
  or every client shares the proxy's IP bucket.
- **CORS** is restricted to `CLIENT_ORIGIN`. CORS isn't authentication.
- **Wallet relay.** The generated `@bsv/wallet-relay` pairing endpoints are
  unauthenticated by design: anyone can create a pairing session. They are
  rate-limited and carry no HA data.
- **Identity trust.** The client discovers the server identity from
  `GET /api/identity` under the TLS authority of `VITE_API_URL`. Pin the key for
  stronger continuity.

## Deviations from the generated scaffold

- `server/src/index.ts` was split into `app.ts` (testable factory) and `index.ts`
  (bootstrap). The `/api/echo` demo route was removed.
- `server/src/bsv/loginRoute.ts` mints a short-lived read-only session after the
  proof verifies.
- The client demo pages (`Home`, `WalletLogin`, `SignedRequestDemo`), the Vite
  template assets and CSS, and `react-router-dom` were removed and replaced by
  one driver page.
- Two fixes to generated client code so `npm run build` passes with
  TypeScript 6: a missing `requireIdentityKey` import in `useWalletLogin.tsx`,
  and a `Uint8Array<ArrayBuffer>` cast in `apiClient.ts`.
- Small accessibility additions to `ConnectWallet.tsx`: `aria-modal`, a labelled
  dialog, and explicit button types.
- Tests use `node --test` with `tsx` (server) and Node type stripping (client).
  The scaffold ships no test runner.
- The generated ESLint config reports errors in generated files, so lint isn't
  part of CI.
