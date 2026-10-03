# Ongoing credits to the last registered driver

An administrator can authorise ongoing operator-funded session credits.
This is a separate authority from a driver's signed spending approval. It
never authorises the operator to debit a driver without that driver's mandate.

## Recipient selection

The configured recorder is the only source of eligible sessions. At activation,
the administrator confirms the latest verified registration by its budget ID
and exact receiving address, and may explicitly include one retained session.
That initial session can have started before registration or activation.
No other earlier session is automatically included.

For later sessions, select the most recent receiving registration made before
the session opened. Fix the recipient and conversion rate when assigning the
session. A later registration does not redirect an already assigned session.
The latest registration must pass the original driver's signature and BRC-29
receiving-proof checks. Manual address text fields are never a routing source.
A revoked or invalid latest registration blocks selection; it does not silently
fall back to another driver.

This deliberately assumes the last registered driver remains the intended
recipient for subsequent sessions. It does not prove who connected the vehicle.
Use it only where the operator accepts that assumption. Disable the policy when
changing this operating arrangement.

## Payment boundaries

- Pay only a negative, fully priced final net account after session closure.
- Freeze the configured `sat/AUD` sensor value when assigning a recipient.
- Cap operator spend at 1,000 sat per session, including the live size-based fee.
  There is no separate fee ceiling; see the [fee policy](operator-fee-policy.md).
- There is no cumulative or daily cap. The policy continues until disabled.
- Preserve fixed account hashes, exact outputs, confirmed funding checks and
  exclusions shared with manual credits and driver collection.
- Queue unfunded unsigned credits. Never replace or rebroadcast a signed
  transaction after a timeout or uncertain outcome.
- Reconcile submitted transactions even after disabling the policy.
- Revoking the receiving registration stops new signatures for its routes.
  Expiry of a driver spending invitation does not expire the separate operator
  credit policy or authorise any new driver debit.

The fixed fee is a demonstration setting, not a market fee estimate. Metered
accounts remain provisional sensor-derived accounts, not certified bills.

## Operator dashboard

Overview shows the route for the current credit session. Payments shows the
latest receiving address, each session's fixed address and conversion, funding
or account blockers, and a **Stop ongoing driver credits** button.
Stopping this policy does not disable separately approved session credits.
The master automatic-credit stop control pauses both sources of new credits.

Drivers explains that matching a spending approval is separate from ongoing
operator credits. Spending approval cannot be matched to a session that began
before the driver signed it.

## Driver receipts

The private page for the receiving registration lists its ongoing credits.
The driver reconnects the same wallet to import confirmed receipts. The server
checks that each requested credit belongs to that registration; the page checks
the returned session and transaction IDs, and the SDK verifies recipient,
amount, transaction and Merkle proof before wallet import.

Receipt import does not send another payment. Access is capability-scoped,
including after spending approval expires, so the driver can receive completed
credits without giving new spending authority.

### Wallet transaction title

Newly imported credits include total session energy and the average net credit,
for example `EV session credit | 10.00 kWh exported | avg net credit A$0.2800/kWh`.
For two-way sessions, total energy is import plus export, with both amounts shown.
The average is the settled AUD credit divided by total energy. It excludes the
BSV transaction fee and is not a charging tariff, export tariff or BSV exchange rate.

Energy comes from the frozen payment account. For older payments, the server
uses retained history only if its hash matches that original account. Missing
or invalid metadata keeps the original generic title rather than inventing
energy or a rate. This is wallet description metadata, not a change to the
transaction, amount or recipient. Previously imported wallet entries are not
automatically renamed; importing a receipt again is not used to force a rename.

## Configuration

Use administrator-only `bsv_settlement.configure_ongoing_credit` with:

```yaml
config_entry_id: <mainnet wallet entry>
enabled: true
proxy_config_entry_id: <session recorder entry>
conversion_rate_entity: <sat per AUD sensor>
initial_session_id: <explicitly authorised initial session, optional>
expected_budget_id: <latest verified receiving registration>
expected_recipient_address: <exact registered receiving address>
confirm_ongoing_mainnet_credits: true
```

To stop, supply only the entry ID and `enabled: false`. Stored routes and
transaction evidence remain for reconciliation. Re-enabling is a fresh policy
activation; unsigned routes from an earlier activation require separate review.

## Validation

Tests use fictional keys and a recording provider. They cover explicit initial
scope, historical exclusions, recipient changes, frozen conversion, caps,
credit-only direction, registration tampering/revocation, pricing failures,
policy disable, receipt isolation, funding queues, restart replay and shared
duplicate-payment exclusions. No real-funds test is part of deployment.
