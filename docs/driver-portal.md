# Static driver portal and wallet sign-in

The stable entry point is `/bsv_settlement/driver/index.html`. A driver signs in with a wallet to view retained charging sessions linked to that wallet identity, without receiving a new private URL for each session.

One top-level Sign in action coordinates identity, a new operator-issued weekly
budget if needed, receiving registration, automatic receipts and per-session debit
collection. Wallet status distinguishes each permission; history and technical
options are collapsed. All wallet matching uses the complete identity key.

## Authority boundary

The login signature proves identity and requests settlement under independently
signed budgets; it cannot itself create or increase a spending budget. The single
Sign in journey can separately sign a displayed operator invitation, register
receiving details and start collection. Existing fee/amount limits, session
ownership, holds and one-use signing permits remain mandatory. It does not start
charging or guarantee payment while the page or wallet is offline.

The portal only lists records with verified historical driver signatures. It resolves operator credits to their original receiving registration, not the last registered driver. Expired or revoked spending permission does not remove historical read access. Unattributed legacy payments and unrelated wallet transactions are excluded.

Existing `#budget=…&token=…` private links retain their legacy interface.
Public `#join=…&key=…` registration links open the unified portal with their exact
terms, never a substituted invitation. New drivers still need an operator-issued
invitation, but complete approval and settlement on the portal without a private
page handoff. The recovery control accepts only same-origin links.

## Login protocol

- The public JSON endpoint issues a random, single-use, 120-second challenge.
- Its canonical payload binds the exact HTTPS origin, browser session, nonce, purpose, issue time and expiry.
- The wallet signs using protocol `[2, "ev portal login"]`, nonce as key ID, and counterparty `anyone`.
- The server independently verifies the derived-key signature against the supplied wallet identity.
- Success rotates the anonymous cookie into a 15-minute, non-sliding authenticated session.
- During user-started connected automation the same wallet supplies a fresh
  challenge signature before login expiry. This rotates login, not spending
  authority. Cookie restoration alone does not start debit collection.
- The `__Host-bsv_driver_portal` cookie is Secure, HttpOnly, SameSite=Strict and root-path only. No login token is put in a URL or browser storage.
- Memory-only sessions expire on restart, sign-out, coordinator removal or configured-origin change. The frontend clears private content at expiry.
- Exact Origin and JSON checks apply to every request. Responses are no-store. Wallet actions are disabled in frames.
- Limits are 64 browser sessions, 120 requests per minute globally, 20 KB request bodies and 25 returned sessions per page. These are bounded prototype controls, not a substitute for upstream denial-of-service protection.

The endpoint requires exactly one loaded embedded-mainnet operator coordinator. Multi-operator selection is not included.

## History and receipt sync

History is projected from stored records without ticking collection, refreshing metering, creating quotes or reconciling payments. It shows direction, amount in sat, fee, provider confirmation, wallet receipt acceptance, session references, energy in both directions, available average prices, and metering warnings. Missing historical values are shown as unavailable.

Each session starts as one compact line: local date/time, payment direction and amount, and settlement status. Wider screens also show import/export energy. Tap or keyboard-activate a row to expand its complete details table. Multiple payments remain separate in the expanded table; the summary does not invent a net payment. A `!` flags a metering warning. Expanded rows stay open during refresh, pagination and receipt sync, but this UI state is cleared at sign-out or expiry.

“Sync credits on this page” imports only already-confirmed operator payments belonging to the signed-in identity. Receipt retrieval can refresh provider evidence and cache an existing proof. Reporting acceptance requires the separate existing wallet-signed acknowledgement. Login alone cannot mark a receipt accepted. No new payment is created or broadcast.

Receipt checks run automatically across owned records while the wallet is
available. A page reload may restore history access, but it does not start the
debit worker. Chain confirmation and wallet-reported receipt acceptance remain
distinct.

## Automatic debit collection

Explicit portal sign-in starts the serial coordinator. Owner-scoped debit
endpoints discover eligible completed sessions and delegate to the existing
signed quote, claim, unsigned draft, signing permit and report implementation.
No private capabilities are returned. At most two sessions are checked per pass.
Held, submitted and confirmed attempts are never replaced; a failed wallet
interaction remains latched. Sign-out or loss of verified wallet context stops
new actions. See `unified-driver-signin.md` for acceptance boundaries and tests.

## Current public rates

The sign-in and verified-wallet cards show Buy (Import/ EV Charging) and
Sell (Export/ V2G) rates in AUD/kWh. Negative and zero rates keep their signs
and units. These are indicative current rates, not a fixed quote for a session.

A same-origin, rate-limited `prices` action reads the single configured sensor
proxy's price sources without creating a login, inspecting driver records,
changing authority or making a payment. The response allowlists only rate
values, interval times, estimate/availability flags and a check time. Multiple
or missing proxy configurations return unavailable rather than choosing a site.

The page fetches rates on load, every minute while visible, when returning to
the tab, and with the signed-in Refresh control. Each rate is hidden as
unavailable if missing, malformed, estimated, outside its interval or more
than 90 seconds past the last check. Fetch failure also clears displayed prices.
The two values remain visible after sign-in and never expose session history
before authentication.

## BSV Browser pairing

The portal can create a fresh two-minute QR and pasteable connection URI without an active spending invitation. The encrypted relay belongs to the browser login, transfers through cookie rotation, and closes on sign-out.

Full portal pairing requests `getPublicKey`, `getNetwork`, `createSignature`,
`createAction`, `signAction` and `internalizeAction`. These methods enable the
guarded client workflow, not a native monetary allowance. Wallet prompts still
require the driver's approval where the wallet demands them.

Native mobile BSV Browser interoperability still requires a supervised live test after deployment. Mock/SDK relay tests are not proof of native connection success. Saved expired connections remain invalid; the portal creates a fresh QR rather than restoring an old relay.

## Validation and deployment

Automated coverage includes cookie rotation and expiry, challenge replay and tampering, cross-wallet isolation, original recipient binding, revoked historical approvals, pagination, action denial, independent receipt signatures, and relay ownership.

The offline preview uses fictional keys and data. It exercises real frontend signing with the TypeScript SDK, but mocks server responses and receipt imports, and disables real pairing and payments. Python HTTP tests cover actual backend authentication independently, including verification of an actual TypeScript SDK login signature.

Historical initial-portal validation: the complete existing-plus-portal Python
suite passed (727 tests), followed by two interoperability/ownership tests.
Current unified-flow validation is recorded in `unified-driver-signin.md`.

Browser checks at 1280 px and 375 px covered sign-in, owner history, pagination (25 then 27 records), refresh, sign-out, empty history, rejected login, login expiry, invalid external invitation links, light/dark mode and existing receipt sync. An interrupted receipt report followed by retry produced one mock wallet import and two acknowledgement attempts, with no payment API. Screenshots showed no horizontal overflow or clipped session values. Native QR scanning and real wallet prompts remain deployment acceptance checks, not completed live tests.

Deployment requires installing the complete integration and restarting Home Assistant to register the new view. No migration rewrites payment records. Rollback restores the previous frontend and removes the portal view on restart; legacy private links remain available.
