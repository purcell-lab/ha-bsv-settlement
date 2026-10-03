# Driver payment descriptions

Future driver payments now use the same energy-summary style as operator credits. The description is supplied when creating the wallet action and its payment output; this change does not rename completed wallet entries.

Fictional example:

> EV session payment | 20.00 kWh charged | avg net cost A$0.0320/kWh

The average is the frozen net AUD session account divided by total energy throughput: imported kWh plus exported kWh. For a mixed session, the title shows both directions. An export-only session with a positive net charge is labelled exported, not charged.

The network fee and demonstration sat/AUD conversion are excluded from this energy price. The description is constructed only after the signed collection quote has passed verification.

Missing or invalid legacy energy metadata falls back to the existing generic description. No payment amount, recipient, fee limit, signing permission, broadcast behaviour or historical payment record changes.

## Awaiting block confirmation

When the chain provider returns the matching raw transaction and its complete unmined JSON shape without confirmation or block fields, driver collection now records `provider_unconfirmed` with zero confirmations. The driver heading and operator status say **Awaiting block confirmation**. This is not proof of inclusion in a block.

Explicit null, negative, boolean or string confirmation counts remain errors. Missing or conflicting transaction data and partial block evidence remain errors. Confirmed funding requirements are unchanged.

Reconciliation only reads the existing transaction. It clears the former generic error when valid evidence is obtained and updates to provider-confirmed when the provider supplies a positive integer count. It does not create a new payment or broadcast again.

## QA inventory

- Verify future wallet action and output descriptions from the signed account, including import-only, mixed and export-only energy; exclude network fees.
- Verify missing, invalid and tiny energy metadata does not fabricate an average.
- Verify unmined responses become pending; malformed responses remain errors.
- Verify pending status persists through reload and moves to confirmed without a second broadcast.
- Verify driver and operator waiting text, no retry control and visibility of genuine errors.
- Inspect the fictional pending driver page at 375 px and 1,280 px in light and dark themes.

Local validation passed: 379 Python tests, 59 driver tests and 25 operator tests. Mobile and desktop visual checks passed in both themes. The pending fixture displayed the exact waiting text, hid payment retry, disabled reconnect collection and made zero wallet claim, draft, signature or report calls.
