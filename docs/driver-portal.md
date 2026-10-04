# Static driver portal and wallet sign-in

The stable entry point is `/bsv_settlement/driver/index.html`. A driver signs in with a wallet to view retained charging sessions linked to that wallet identity, without receiving a new private URL for each session.

The verified-wallet panel uses a compact identity preview with visible Refresh, Sync credits and Sign out controls. “Wallet details” shows the access expiry and expands to reveal the full identity and permission explanation. The preview is display-only; all wallet matching still uses the complete key. Verified sign-in is not a claim that the wallet transport is currently connected.

## Authority boundary

Sign-in proves control of a wallet key. It does not authorise spending, reserve funds, create a payment, renew a mandate, change a recipient, register an unclaimed session, or start charging. Existing signed spending approvals and collection safeguards remain separate.

The portal only lists records with verified historical driver signatures. It resolves operator credits to their original receiving registration, not the last registered driver. Expired or revoked spending permission does not remove historical read access. Unattributed legacy payments and unrelated wallet transactions are excluded.

Existing `#budget=…&token=…` private links and `#join=…&key=…` registration links still open the existing approval interface. The no-fragment page now opens the portal instead of automatic public registration. New drivers still require a separate operator invitation. The recovery control accepts only same-origin links.

## Login protocol

- The public JSON endpoint issues a random, single-use, 120-second challenge.
- Its canonical payload binds the exact HTTPS origin, browser session, nonce, purpose, issue time and expiry.
- The wallet signs using protocol `[2, "ev portal login"]`, nonce as key ID, and counterparty `anyone`.
- The server independently verifies the derived-key signature against the supplied wallet identity.
- Success rotates the anonymous cookie into a 15-minute, non-sliding authenticated session.
- The `__Host-bsv_driver_portal` cookie is Secure, HttpOnly, SameSite=Strict and root-path only. No login token is put in a URL or browser storage.
- Memory-only sessions expire on restart, sign-out, coordinator removal or configured-origin change. The frontend clears private content at expiry.
- Exact Origin and JSON checks apply to every request. Responses are no-store. Wallet actions are disabled in frames.
- Limits are 64 browser sessions, 120 requests per minute globally, 20 KB request bodies and 25 returned sessions per page. These are bounded prototype controls, not a substitute for upstream denial-of-service protection.

The endpoint requires exactly one loaded embedded-mainnet operator coordinator. Multi-operator selection is not included.

## History and receipt sync

History is projected from stored records without ticking collection, refreshing metering, creating quotes or reconciling payments. It shows direction, amount in sat, fee, provider confirmation, wallet receipt acceptance, session references, energy in both directions, available average prices, and metering warnings. Missing historical values are shown as unavailable.

Each session starts as one compact line: local date/time, payment direction and amount, and settlement status. Wider screens also show import/export energy. Tap or keyboard-activate a row to expand its complete details table. Multiple payments remain separate in the expanded table; the summary does not invent a net payment. A `!` flags a metering warning. Expanded rows stay open during refresh, pagination and receipt sync, but this UI state is cleared at sign-out or expiry.

“Sync credits on this page” imports only already-confirmed operator payments belonging to the signed-in identity. Receipt retrieval can refresh provider evidence and cache an existing proof. Reporting acceptance requires the separate existing wallet-signed acknowledgement. Login alone cannot mark a receipt accepted. No new payment is created or broadcast.

Sync is explicit in this first portal revision. Load more pages to sync older credits. A page reload may restore a valid server login, but the wallet must be available again for receipt import. Chain confirmation and wallet-reported receipt acceptance remain distinct.

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

Sign-in requires only `getPublicKey` and `createSignature`. The portal wallet proxy does not expose `createAction` or `signAction`. Receipt sync additionally requires `getNetwork` and `internalizeAction`, with an explicit mainnet check. A wallet missing those methods can sign in but cannot import receipts through this channel.

Native mobile BSV Browser interoperability still requires a supervised live test after deployment. Mock/SDK relay tests are not proof of native connection success. Saved expired connections remain invalid; the portal creates a fresh QR rather than restoring an old relay.

## Validation and deployment

Automated coverage includes cookie rotation and expiry, challenge replay and tampering, cross-wallet isolation, original recipient binding, revoked historical approvals, pagination, action denial, independent receipt signatures, and relay ownership.

The offline preview uses fictional keys and data. It exercises real frontend signing with the TypeScript SDK, but mocks server responses and receipt imports, and disables real pairing and payments. Python HTTP tests cover actual backend authentication independently, including verification of an actual TypeScript SDK login signature.

Local validation: the complete existing-plus-portal Python suite passed (727 tests), followed by two additional passing interoperability and expired weekly-child ownership tests. The driver suite passed 84 tests and the operator suite passed 56 tests. CI runs the final combined Python suite.

Browser checks at 1280 px and 375 px covered sign-in, owner history, pagination (25 then 27 records), refresh, sign-out, empty history, rejected login, login expiry, invalid external invitation links, light/dark mode and existing receipt sync. An interrupted receipt report followed by retry produced one mock wallet import and two acknowledgement attempts, with no payment API. Screenshots showed no horizontal overflow or clipped session values. Native QR scanning and real wallet prompts remain deployment acceptance checks, not completed live tests.

Deployment requires installing the complete integration and restarting Home Assistant to register the new view. No migration rewrites payment records. Rollback restores the previous frontend and removes the portal view on restart; legacy private links remain available.
