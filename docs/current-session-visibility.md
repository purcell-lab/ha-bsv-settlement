# Keep current-session collection visible

## Problem and scope

An existing-session spending approval identifies its session in its signed terms.
It does not need the separate binding object used by a future reservation.
The driver page treated a missing binding as an unassigned session. When the
driver also had ongoing operator credits, that overview could hide the current
collection, including its reviewed recovery button.

The fix limits the ongoing overview to an accepted, unbound future reservation
whose collection state is `waiting_for_operator_binding`. All other current
settlement states retain the primary view. The current settlement appears before
the separate ongoing credits section. Credit status messages describe operator
credits, not driver collection.

This is a presentation change only. It does not rotate or recover an invitation,
release a hold, change a mandate, alter transaction validation, or send a payment.
Existing private links and explicit recovery confirmation remain required.

## QA inventory

- Existing-session approval, no binding, reviewed collection, and a different
  ongoing session: display the current amount, fee cap and enabled review button.
  Keep the ongoing session visible separately without changing the headline.
- Cancel the review dialog: no claim, draft, signing or submission.
- Confirm in the isolated fixture: one simulated claim and failed draft; retain
  the diagnostic and return to the held state without another payment control.
- Poll after cancellation and after the failed draft: no automatic retry.
- Held and interrupted-connection scenarios with ongoing credits: preserve the
  held-state message and separate connection diagnostic.
- Future reservation waiting for binding: keep the existing ongoing overview.
  Unit tests also cover bound reservations, legacy terms, terminal states and
  unaccepted approvals.
- Mobile at 375 px and desktop at 1280 px, light and dark: inspect the current
  settlement, secondary credit section and expanded references for clipping.
- Pairing preview: create and disconnect the fictional QR connection; this must
  not claim a collection or hide its recovery action.

## Reproduce

Run the Python tests and both frontend test suites. Rebuild the production driver
bundle and the isolated recovery and pairing previews with the existing build
scripts. Serve `preview/` locally and open `recovery/index.html`.

The default scenario reproduces an existing-session approval with `binding:null`,
a reviewed collection and another ongoing session. The scenario selector also
provides held, reviewed-only, offline and future-reservation cases.

All preview identities, references and keys are fictional. Requests are handled
in memory. The preview wallet rejects draft creation and cannot sign or broadcast
a transaction. These checks do not validate a native BSV Browser wallet or live
settlement.

## Validation result

The local suite passed: 321 Python/Home Assistant tests, 53 driver JavaScript
tests and 20 operator JavaScript tests, for 394 total. Python compilation and
`git diff --check` also passed. The driver changes add 14 regression tests.

The isolated Chromium checks passed for the reviewed collection with ongoing
credits, cancellation, one failed mock draft, subsequent polling, held state,
offline diagnostics, future-reservation overview and pairing/disconnect fallback.
No signing or broadcast occurred. Mobile and desktop screenshots were inspected
in light and dark themes, including expanded references. No horizontal page
overflow or uncaught page errors were observed.

The current settlement now precedes ongoing credits in both DOM and visual order.
Live deployment, native wallet connectivity and successful collection are not
established by these mock tests.
