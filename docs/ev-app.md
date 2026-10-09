# EV app: HA engine, new front door

`apps/ev-app/client` is a React driver client built with the official
`create-bsv-app@1.1.2` framework. Its build is committed to
`custom_components/bsv_settlement/frontend/app/` and **served by the
integration** at `/bsv_settlement/app/index.html`, next to the existing driver
page at `/bsv_settlement/driver/index.html`. It ships in the HACS payload. The
only Python change is one more `StaticPathConfig` in `__init__.py`.

The app has no server and no Home Assistant token. It uses the integration's
existing same-origin driver portal API (`/api/bsv_settlement/portal`) and its
existing sign-in cookie. Home Assistant stays the single source of financial
truth and performs every check it already performs for the driver page.

Setup, the wire contract, build and provenance are in
[apps/ev-app/README.md](../apps/ev-app/README.md).

## Milestone plan

| Milestone | Scope | Status |
|---|---|---|
| **M1: read-only, hosted** | Public rates (`prices`). Wallet sign-in through the portal's existing wallet-signature challenge (`challenge`, `login`). The driver's own sessions and payments (`sessions`). Sign out (`logout`). No other portal action can be sent. | This change |
| **M2: write flows through existing portal actions (gated)** | Add the driver write flows (registration, budget approval, collection, credit receipts, monthly) by calling the portal actions the driver page already uses, behind the same server-side checks in `portal.py`, `portal_debits.py`, `portal_registration.py` and `monthly_portal.py`. No new server authority. Each flow needs parity tests against the driver page and owner approval. | Not started; gated |
| **M3: wallet-relay and BRC-103 (gated)** | Evaluate `@bsv/wallet-relay` mobile pairing against the custom browser pairing, gated on BSV Browser acceptance evidence comparable to [bsv-browser-acceptance.md](bsv-browser-acceptance.md). Evaluate `@bsv/auth` BRC-103 sign-in and signed requests. This needs a Python BRC-103 verifier in the integration, and a relay endpoint inside HA. | Not started; gated |

## M1 guarantees

- Same origin only. Requests are POST JSON to `/api/bsv_settlement/portal` with
  `credentials: 'same-origin'`. No token, `Authorization` header, local storage
  or readable cookie is used. The sign-in cookie is the portal's
  `__Host-bsv_driver_portal` cookie (Secure, HttpOnly, SameSite=Strict, path `/`).
- Five actions only: `prices`, `challenge`, `login`, `sessions`, `logout`. The
  client refuses others before any request, and a test scans the source for
  every other portal action name.
- The challenge is validated before the wallet is asked to sign. The signature is
  made with the same `createSignature` arguments as the driver page (checked
  against the driver module in a test) and verified locally before it is sent.
  No transaction, spending or payment wallet call exists in the app.
- Responses are shape-checked. A sign-in lifetime outside 1 to 900 seconds is
  rejected. A `sessions` identity that differs from the signed-in identity clears
  the page. A 401 clears private data. Private data is also cleared when the
  sign-in lifetime ends.
- Unknown values show "Unavailable", never 0. Provisional amounts are labelled,
  use the driver page's rules (fresh meter, valid session rate, decimal
  half-up) and say they are not payment requests. The session conversion rate
  is labelled "demonstration rate, not market FX". Status wording is ported
  from the driver page.
- Accessibility: skip link, landmarks, live status region, visible focus,
  44 px targets, works at 360 px, follows the system light or dark setting.
- The committed build is reproducible and checked in CI. It has no source maps
  and carries its third-party licences.

## Risks and notes

- **Origin.** The portal compares the browser's `Origin` with Home Assistant's
  configured external URL (`external_origin`). The app must be opened through
  that external HTTPS URL, as for the driver page. Opened by LAN address or the
  internal URL, `prices` and sign-in return 403.
- **Shared sign-in with the driver page.** Both pages use the same portal
  session cookie (path `/`). Signing in on `/bsv_settlement/app/` creates the
  same server-side portal sign-in as the driver page, with the same scope
  (`read_own_sessions_sync_receipts_and_collect_signed_session_budgets`) and
  the same 15 minute lifetime. The driver page does not read that cookie on load
  and still asks the wallet to sign in, but its own `challenge` and `login`
  reuse and rotate the same cookie. Signing out on either page ends the sign-in
  for both. If another wallet signs in from the other page, this app detects the
  identity change on its next `sessions` call and clears. The scope is wider
  than what this read-only app uses. It authorises nothing without the driver's
  separately signed budget or monthly authority, which the server still checks.
  The wallet prompt text is the driver page's, which describes that full scope.
  This is accepted for M1 because the app adds no new server authority.
- **Directory URL.** HA static paths do not serve a directory index. Link to
  `/bsv_settlement/app/index.html`, not `/bsv_settlement/app/`.
- **Bundle size.** About 650 kB minified (about 200 kB gzip), mostly
  `@bsv/sdk` `WalletClient` and React. The driver bundle is about 495 kB.
- **No CSP.** Like the driver page, no Content Security Policy is set, because
  `WalletClient('auto')` probes local wallet endpoints.
