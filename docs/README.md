# Design and implementation documents

Development code now combines a signed pre-session spending budget, sensor-proxy
session accounting, browser-open driver collection and optional server-side
automatic operator credits. A charger-enforced budget gate remains a target, not
an implemented control. The original no-budget mock documentation remains below.

## Current development

- **[Automatic operator credits](automatic-operator-credits.md):** negative
  balances paid without per-payment approval under a bounded operator policy.
- **[Driver spending approval](driver-session-budget.md):** private invitation,
  dynamic pricing terms, BSV Browser connection and session binding.
- **[Sensor session proxy](sensor-session-proxy.md):** provisional import/export
  metering, interval pricing and proxy transaction IDs.
- **[Manual exception review](session-payment-review.md):** separate reviewed
  requests and credits, mutually exclusive with automatic session settlement.

## Original mock baseline

- **[Settlement interface](settlement-interface.md):** proposed responsibility split, HA actions and entities, pricing rules, API contract, approval and recovery safeguards.
- **[Settlement sequence](settlement-sequence.md):** no-budget flow and explicit mock implementation boundary.
- **[Setup guide](../README.md):** runnable mock service and HA scaffold.
- **[API schema](../openapi.json):** generated from the implemented service.
- **[Verification results](../TEST_RESULTS.md):** 27 passing tests for v0.1.2 and an HTTP demonstration, with untested boundaries identified.

The design document describes the target interface. The implementation deliberately uses `mock_received` and `mock_confirmed`, adds the HA `add_interval` action and provides synthetic identities and manual mock-approval endpoints. No live-wallet capability should be inferred from the design.

## Research and history

- **[Comparable wallet systems](research/wallet-micropayments-comparison.md):** source-cited research, originally conducted against the earlier budget-first brief.
- **[Archived concepts and visuals](archive/README.md):** historical feasibility, budget-first design, sequence and infographic.

## Next implementation milestone

Map the installed OCPP integration's directional energy counters and final session readings into the ledger. Independently verify the dynamic tariff source, then implement and test a small operator-to-driver payment with the selected real wallet before enabling the reverse direction.

Repository publication does not install this software in Home Assistant or deploy a running wallet service.
