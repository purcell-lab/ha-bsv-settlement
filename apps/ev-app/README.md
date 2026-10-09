# EV app (hosted driver client)

A React 19 / Vite driver client, built into the Home Assistant integration and
served by it, exactly like the existing driver page:

| Page | URL on the owner's HTTPS Home Assistant domain | Source |
|---|---|---|
| Driver portal (existing) | `/bsv_settlement/driver/index.html` | `frontend/driver/` |
| **EV app (this)** | **`/bsv_settlement/app/index.html`** | `apps/ev-app/client/` |

Open `index.html` explicitly. Home Assistant static paths do not serve a
directory index, so a bare `/bsv_settlement/app/` is refused.

There is **no app server and no Home Assistant token**. The browser talks only to
the integration's existing, same-origin driver portal API
(`POST /api/bsv_settlement/portal`, `DriverPortalView` in
`custom_components/bsv_settlement/portal.py`). Home Assistant stays the single
source of financial truth.

## What milestone 1 does (read-only)

1. Shows the public buy and sell rates (`prices`). No wallet or cookie is needed.
2. Signs in with the driver's BSV wallet (`challenge`, then `login`).
3. Shows the driver's own sessions and payments (`sessions`, 25 per page).
4. Signs out (`logout`).

Those five actions are the only ones the client can send. It refuses every other
portal action (`pairing_*`, `registration_*`, `debit_*`, `monthly_*`,
`credit_receipt`, `station` and so on) before making a request, and a unit test
scans the source to keep it that way. It has no payment, approval, collection,
credit, waiver, registration or charger controls. For those the page links to
the driver portal.

Display rules: an unknown or invalid value shows "Unavailable", never 0. A
provisional amount for an open session is labelled "Provisional", is built only
from a fresh meter reading and a valid session rate, and says it is not a
payment request. The session conversion rate is labelled "demonstration rate,
not market FX". Rate cards expire after 90 seconds without a fresh check.

## Wire contract (same as `frontend/driver/portal.js`)

Every request is `fetch('/api/bsv_settlement/portal', {method: 'POST',
credentials: 'same-origin', cache: 'no-store', referrerPolicy: 'no-referrer',
headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action, ...})})`.
The browser adds `Origin`. The server requires it to equal Home Assistant's
configured external URL.

| Action | Request fields | Response fields used |
|---|---|---|
| `prices` | none | `checked_at`, `import`/`export`: `available`, `estimate`, `aud_per_kwh`, `start`, `end` |
| `challenge` | none | `payload`, `protocolID`, `keyID` (sets the short-lived portal cookie) |
| `login` | `identity`, `payload`, `signature` | `identity`, `expires_in` (rotates the cookie, 15 minutes) |
| `sessions` | `offset` | `identity`, `sessions[]`, `total`, `expires_in` |
| `logout` | none | `signed_out` (clears the cookie) |

`sessions[]` fields shown: `session_key`, `transaction_id`, `opened_at`,
`ended_at`, `import_kwh`, `export_kwh`, `net_amount_aud`, `meter_updated_at`,
`satoshis_per_aud`, `quality_flags`, `closure.state`, and for each of
`transactions[]`: `id`, `direction`, `state`, `amount_sats`, `fee_sats`, `txid`,
`confirmations`, `created_at`, `wallet_receipt_status`, `wallet_imported_at`.

## Wallet sign-in

The scaffold's BRC-103 `@bsv/auth` login and signed requests were removed.
Home Assistant has no `@bsv/auth` verifier. The app uses the portal's existing
wallet-signature challenge instead, ported unchanged from `signPortalLogin()` in
`frontend/driver/portal-model.js`:

- The challenge is checked first: exact keys, `sign_in_driver_portal`, version 1,
  this page's origin, the portal scope, nonce and browser binding format, and a
  lifetime of at most 120 seconds.
- `WalletClient` from `@bsv/sdk` (`window.CWI` inside BSV Browser, otherwise
  `auto` for Metanet Desktop) gets the identity key, then calls
  `createSignature({protocolID: [2, 'ev portal login'], keyID: <nonce>,
  counterparty: 'anyone', data: <payload bytes>, description})`.
- The signature is verified locally before it is sent.

A test signs the same challenge with the driver page's own module and asserts
identical `createSignature` arguments and an identical proof.

Wallet compatibility is the same as the driver page: Metanet Desktop on the same
computer, or the BSV Browser in-app browser. The scaffold's mobile QR relay
(`@bsv/wallet-relay`) needs a server and is not included.

## Develop, test, build

```sh
cd apps/ev-app/client
npm ci
npm test        # node --test unit tests (fake fetch, no network)
npm run build   # tsc -b, then vite build into custom_components/bsv_settlement/frontend/app/
```

For the driver-parity sign-in test, run `npm ci` in `frontend/driver` first. CI
always does.

The build is reproducible. File names are fixed (`index.html`, `app.js`,
`index.css`, `favicon.svg`, `THIRD-PARTY-LICENSES.txt`), there is one chunk and
no source maps, and dependency versions are pinned exactly. Rebuilding must
leave `git diff -- custom_components/bsv_settlement/frontend/app` empty. CI
checks this in the `EV app (apps/ev-app)` job. Commit the rebuilt output with
any client change.

Licences: legal comments stay inline in `app.js`. `THIRD-PARTY-LICENSES.txt`
carries the `@bsv/sdk` licence, its `THIRD_PARTY_NOTICES.md` and `LICENSES/`
texts, and the React, React DOM and scheduler licences.

`npm run dev` serves the client from Vite, but sign-in only works from the real
Home Assistant origin, because the portal checks `Origin` against HA's external
URL and its cookie is `Secure` and `__Host-` prefixed.

## Provenance

Scaffolded with `create-bsv-app@1.1.2` (React + Express, wallet-connect,
wallet-login and signed-requests). `bsv-scaffold.json` records that original
selection. The Express half of the scaffold (server, HA adapter, sessions,
nonce store, rate limiter, relay service) was removed on purpose, together with
the BRC-103 login, signed-request client and relay pairing code. Only the
client and the `WalletClient` wallet-connect path remain.

See [docs/ev-app.md](../../docs/ev-app.md) for guarantees, risks and the
milestone plan.
