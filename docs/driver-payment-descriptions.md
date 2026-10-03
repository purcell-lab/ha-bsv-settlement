# Driver payment descriptions

Future driver payments now use the same energy-summary style as operator credits. The description is supplied when creating the wallet action and its payment output; this change does not rename completed wallet entries.

Fictional example:

> EV session payment | 20.00 kWh charged | avg net cost A$0.0320/kWh

The average is the frozen net AUD session account divided by total energy throughput: imported kWh plus exported kWh. For a mixed session, the title shows both directions. An export-only session with a positive net charge is labelled exported, not charged.

The network fee and demonstration sat/AUD conversion are excluded from this energy price. The description is constructed only after the signed collection quote has passed verification.

Missing or invalid legacy energy metadata falls back to the existing generic description. No payment amount, recipient, fee limit, signing permission, broadcast behaviour or historical payment record changes.
