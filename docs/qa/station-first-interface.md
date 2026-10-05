# Station-first interface (S4)

Part of #79; stacked on S3. Offline preview only: fictional wallet, sessions and
monthly authority. No live wallet, Home Assistant, chain or payment.

## What changed for the driver

- **Station · My charging · History** tabs replace the three-step indicator.
  Station shows public station identity, live buy/sell rates with the negative-rate
  explanation, the monthly terms, one **Authorise monthly charging** action and a
  permanent public station QR with its exact link text and a copy button.
- **My charging** shows the owned current session: OCPP status, energy to and from
  the EV, average buy/sell AUD/kWh, net account in AUD and the provisional sat
  amount from PR #78's projection (operator conversion helper), current rates and
  monthly readiness. Unknown values read Unavailable, never zero.
- **History** is the existing compact, expandable owner history.
- **Wallet** drawer (under the tab bar): identity, connection, monthly authority,
  wallet monthly permission (server evidence only), receiving registration,
  Limit / Spent / Reserved / Remaining for the wallet month, cancellation with a
  confirmation step, refresh, retry receiving and sign out.
- Returning drivers keep a visible **Sign in to view my charging** on Station.

The shared account projection (`account-projection.js`) is built from the operator
card's `energyMetrics` and `provisionalDisplay`; tests assert both give equal
energy, averages, conversion and label for the same session.

## QA inventory and results

`frontend/driver/qa-station-preview.mjs` against `preview/portal`: **57/57 passed**.

- Station, monthly off and on: Station tab default; buy 0.2850 and negative sell
  −0.0520 shown with sign; terms name 30,000 sat including fees; authorise is the
  primary action only when enabled; station QR text equals the public page link and
  has one copy button; no private history before sign-in; no page errors.
- One action (fresh driver, active session): exact signed terms shown before the
  wallet with focus moved to them; login → challenge → accept in order, accepted once;
  readiness ready; OCPP Charging; 0.000 kWh in / 0.530 kWh out; average sell
  $0.1132/kWh; "6 sat provisional"; no create/sign/commit/reserve call.
- Wallet drawer: keyboard open focuses its heading; Remaining 28,505 sat; permission
  verified; cancellation needs confirmation; afterwards "revocation not verified";
  Escape closes and returns focus to the Wallet button.
- Wallet permission unverified: not ready and explains why; authorise not offered
  when an authority exists.
- Keyboard: ArrowLeft/ArrowRight/Home/End move between tabs with focus; only the
  selected tab is in the tab order; signed-out panels explain sign-in.
- 390 px mobile and 1,365 px desktop, light and dark: no horizontal overflow on
  Station, My charging, History or with the drawer open. Screenshots inspected.

Defects found and fixed during QA:

- A wallet substrate behind `WalletClient` without `isAuthenticated` stopped the
  action; the lock query is now optional and identity remains the gate.
- Sign-in mid-setup switched tabs and hid the terms confirmation.
- The plain sign-in for returning drivers was hidden in "Wallet options and help".
- Focus fell to the page after cancellation, so Escape no longer closed the drawer.
- The drawer opened at the bottom of the page on mobile; duplicate energy text.

## Not covered here

Native Metanet / BSV Browser behaviour, screen-reader audio checks and real
payment flows are S5 gates. Monthly charging remains disabled at runtime.
