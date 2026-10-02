# Home Assistant and BSV wallet settlement

## Minimal proof-of-concept interface

Prepared for Mark Purcell | 2 October 2026 | Draft 0.1

**Proposal:** settle the net cost or credit of an OCPP session through a bound BSV wallet. Home Assistant calculates the energy account; a separate wallet service handles payment approval, signing, receipt delivery and transaction tracking.

This draft replaces the earlier budget-first design. It contains no budget approval, spending allowance, reserved funds, prepaid balance or charge/refund cycle. It is a proposed implementation contract, not a working integration or a claim of compatibility already tested.

## Scope and boundaries

The first demonstration supports one charger, one session at a time, one known driver wallet and one operator wallet. The driver and operator wallets must be distinct and separately controlled. Start in mock mode, then test supported wallet combinations with small, explicitly approved amounts.

| Component | Responsibility |
|---|---|
| Existing OCPP integration | Session control and directional energy readings. Leave the existing direct connection unchanged. |
| New HA integration, proposed domain `bsv_settlement` | Durable session ledger, interval pricing, final calculation, settlement requests and status entities. |
| New settlement service | Immutable settlement records, fiat-to-satoshi quote, payer approval, payment orchestration, duplicate protection and receipts. |
| Driver wallet | Approves and signs a driver payment; receives an operator credit. |
| Operator wallet | Receives a driver payment; approves and signs an operator credit. |

No payment component issues charging commands, changes grid envelopes or substitutes for electrical protection. An unpaid account does not retrospectively change the energy session.

The candidate operator-wallet backend is `bsv-wallet-cli`, whose documentation describes an external BRC-100 HTTP service with transaction creation, signing and payment-receipt methods; it does not provide this HA integration or the proposed settlement API ([wallet server documentation](https://github.com/Calhooon/bsv-wallet-cli)).

## End-to-end flow

1. Select a previously verified driver-wallet binding and associate it with the session.
2. Run the charging session through the existing OCPP integration.
3. Persist timestamped import and export meter readings and the applicable interval prices.
4. On session completion, reconcile final meter readings and final prices. Do not settle while either is incomplete.
5. Freeze a ledger revision and calculate the signed net AUD amount.
6. Prepare one immutable settlement and show its AUD amount, BSV amount, payer, recipient and fee treatment.
7. The payer approves the exact payment. For a debit, this is the driver; for a credit, it is the operator. Manual payer approval in both directions keeps the first live demonstration simple.
8. Transfer the payment and remittance data, verify recipient acceptance, and track chain confirmation separately.
9. Return a receipt to HA and retain any unsettled or ambiguous result for reconciliation.

Payment approval happens after charging, not before it. The operator therefore bears non-payment risk for a net debit; this is an explicit limitation of the simplified demonstration.

### Logical sequence

```text
OCPP adapter       HA ledger          Settlement service       Payer / recipient
     |                 |                      |                       |
     |-- readings ---->| persist and price    |                       |
     |-- session end ->| reconcile and freeze |                       |
     |                 |-- prepare ---------->| lock payment quote    |
     |                 |<-- prepared ---------|                       |
     |                 |-- request ---------->|-- approval request -->| payer
     |                 |                      |<-- approve and sign ---|
     |                 |                      |-- payment + proof ---->| recipient
     |                 |                      |<-- receipt acceptance --|
     |                 |-- poll ------------->|                       |
     |                 |<-- receipt/status ---|                       |
     |                 |                      | track confirmation    |
```

“Payer” means the driver for a net cost and the operator for a net credit. The wallet service must not sign on behalf of a remotely bound driver wallet.

## Home Assistant integration

### Proposed files

```text
custom_components/bsv_settlement/
  manifest.json
  __init__.py
  config_flow.py
  const.py
  api.py
  coordinator.py
  ledger.py
  ocpp_adapter.py
  sensor.py
  services.yaml
  strings.json
  translations/en.json
tests/
  test_ledger.py
  test_settlement_client.py
  test_recovery.py
```

Home Assistant documents `manifest.json` and `__init__.py` as the minimum integration files, `services.yaml` for action descriptions, and a coordinator as a common approach to coordinated polling ([integration file structure](https://developers.home-assistant.io/docs/creating_integration_file_structure/)).

Use a unique custom domain, asynchronous HTTP calls, a configuration flow and a single coordinator polling the settlement service. Private keys, wallet seeds and raw signing authority must not enter HA configuration, entity attributes or logs.

### Configuration

| Field | Proposed behaviour |
|---|---|
| `service_url` | Direct HTTPS connection to the settlement service, or loopback HTTP only when actually sharing the same host network namespace. No reverse proxy required. |
| `api_token` | Credential scoped to preparing/requesting settlements and reading their status, not unrestricted wallet signing. Redact from logs and diagnostics. |
| `mode` | `mock` by default; switching to live requires explicit operator action. |
| `charger_mapping` | Explicit session ID, connector, directional cumulative meter and session-state mappings from the installed OCPP integration. |
| `tariff_mapping` | Timestamped import and export prices, currency, units and final/provisional status. |
| `operator_binding_id` | Verified operator-wallet reference on the settlement service. |
| `driver_binding_id` | Known driver-wallet reference for this single-driver demonstration. |

The exact OCPP entity names, export registers and end-of-session signals must be checked against the installed integration and charger. Do not invent universal entity IDs or infer export from a changing signed net-power sensor when directional energy counters are available.

### Proposed actions

All actions require `config_entry_id`; session-specific actions also require `session_id`. These names are new interfaces proposed here.

| Action | Purpose |
|---|---|
| `bsv_settlement.bind_session` | Attach a verified driver binding to a session. Rebinding after energy capture requires review; rebinding after preparation is prohibited. |
| `bsv_settlement.prepare_session` | Validate/freeze the ledger and create the immutable quoted settlement. Does not send money. |
| `bsv_settlement.request_payment` | Present the prepared settlement to its payer for approval. Repeated calls return the same payment request. |
| `bsv_settlement.refresh` | Reconcile known settlement and transaction status. Never creates a replacement payment. |

The normal automation can bind the known wallet at session start and prepare after final reconciliation. For initial live tests, `request_payment` should be a deliberate operator action.

Register integration actions in `async_setup`, rather than `async_setup_entry`, and validate the requested config entry at call time; Home Assistant documents this registration pattern and supports optional JSON response data for actions ([integration service actions](https://developers.home-assistant.io/docs/dev_101_services/)).

### Proposed entities and event

Use a fixed small set per charger, rather than creating permanent entities for every session:

| Entity suffix | Value |
|---|---|
| `session_import_energy` | Current/last session import, kWh |
| `session_export_energy` | Current/last session export, kWh |
| `session_net_amount` | Signed AUD: positive means driver owes, negative means operator owes |
| `settlement_status` | Current settlement state |
| `settlement_amount` | Absolute integer satoshis for the quoted settlement |

Expose `session_id`, `settlement_id`, `direction`, `quote_id`, `txid` and short error code as limited attributes. Keep full intervals, identity keys, payment requests and proofs in the durable ledger/service database, not the HA state machine.

Emit `bsv_settlement_status_changed` when a meaningful state transition occurs. Events are notifications, not a durable queue; restart recovery must read persistent records.

## Energy account and currency conversion

### Calculation

For each interval:

```text
interval_net_aud =
    import_kwh × import_price_aud_per_kwh
  - export_kwh × export_price_aud_per_kwh

session_net_aud = sum(interval_net_aud)
```

Use decimal arithmetic, preserve calculation precision, and round the final AUD amount once to cents using an explicitly configured rule: `ROUND_HALF_UP` for this demonstration. Preserve the unrounded total and rounding adjustment in the audit record.

Prices are signed. A negative import price can create a credit, and a negative export price can create a charge. Payment direction comes from the final net amount, not simply from whether the session imported or exported.

Split energy at tariff boundaries using measured interval data. If readings cannot establish a defensible allocation, mark the session `needs_review`; do not silently invent interval quantities. Reject counter resets, negative directional deltas, unexplained gaps, stale/provisional prices and overlapping intervals.

### Minimal interval record

```json
{
  "session_id": "site01:charger01:connector1:transaction42",
  "start": "2026-10-02T02:00:00Z",
  "end": "2026-10-02T02:05:00Z",
  "import_wh": 250,
  "export_wh": 0,
  "import_price_aud_per_kwh": "0.300000",
  "export_price_aud_per_kwh": "0.600000",
  "price_status": "final",
  "tariff_version": "demo-v1",
  "meter_quality": "validated"
}
```

Use UTC timestamps, retain the local timezone for presentation, and define intervals as start-inclusive/end-exclusive. Final directional sums must reconcile with the session's start/end counters before preparation.

### BSV amount

The settlement service owns the conversion quote. For the mock demonstration, use an explicitly labelled synthetic fixed rate, such as `10,000 satoshis per AUD`; it is a test fixture, not a market price.

For live tests, select and record an agreed rate source or explicit manual rate before payment. Freeze `quote_id`, source, timestamp, expiry, rate direction and rounding rule; the payer approves the resulting integer satoshi amount. An expired quote cannot be paid: it requires a new version and fresh approval.

For example, a net AUD credit of `-0.30` at the synthetic rate becomes an operator payment of `3,000` satoshis to the driver. A net AUD debit of `0.60` becomes a driver payment of `6,000` satoshis.

For this POC the payer covers the network fee in addition to the quoted energy payment, and the recipient receives the quoted amount. Show or cap that fee within the payer's transaction approval. Do not silently subtract it from a driver credit.

A zero rounded AUD amount produces `no_payment_due`. A nonzero amount that rounds to zero satoshis or falls below a wallet/provider minimum produces `below_minimum`, not a false paid status.

## Proposed wallet-service API

This REST contract belongs to the new settlement service. It is not an existing BRC-100 interface. The service wraps wallet-specific methods behind a stable energy-settlement boundary.

All endpoints require authentication. Use JSON, strict schema validation, bounded payload sizes and persistent storage. A single SQLite database is sufficient for the single-instance POC, provided state changes and uniqueness checks use database transactions.

### Endpoint summary

| Method and path | Behaviour |
|---|---|
| `GET /v1/health` | Mode, network, backend availability and supported features. No keys or balances. |
| `GET /v1/wallet-bindings/{binding_id}` | Verification status, display label, network and supported send/receive capabilities. |
| `PUT /v1/settlements/{settlement_id}` | Create an immutable prepared settlement or return the existing identical record. Never broadcast. |
| `POST /v1/settlements/{settlement_id}/request-payment` | Create/reuse the payer approval request. May return a wallet-specific approval URL or code. |
| `POST /v1/settlements/{settlement_id}/quotes` | Replace an expired quote before submission, preserving the immutable energy account and invalidating prior unspent approval requests. |
| `GET /v1/settlements/{settlement_id}` | Status, required next action and receipt fields. |

Wallet binding is provisioned once in the service's setup interface. Verify identity-key possession using a nonce-based signature with the selected wallet, or document a supervised local-wallet binding; merely typing an address is not proof of control. Expiring challenges must bind the service origin and expected identity. Authentication and verification of any wallet callbacks are responsibilities of the service adapter.

The remote driver's wallet approval and receipt-delivery mechanism is a required interoperability test, not assumed to exist because the local operator wallet supports HTTP. For the initial supervised demonstration, opening the service's payment screen on the driver's device is acceptable. The screen invokes the wallet through its supported interface; no invented universal deep-link scheme is assumed.

### Prepare request

The `settlement_id` is allocated once and durably stored by HA before its first request. It is an opaque UUID, not an OCPP transaction number.

```http
PUT /v1/settlements/7dd9a349-8889-4f80-976b-b3ebd51b80b4
Authorization: Bearer <redacted>
Content-Type: application/json
```

```json
{
  "session_id": "site01:charger01:connector1:transaction42",
  "ledger_revision": 1,
  "ledger_sha256": "<64-character digest of frozen canonical ledger>",
  "driver_binding_id": "driver-demo-01",
  "operator_binding_id": "operator-demo-01",
  "currency": "AUD",
  "net_amount_minor": -30,
  "import_wh": 3000,
  "export_wh": 2000,
  "pricing_summary": {
    "import_amount_aud": "0.90",
    "export_credit_aud": "1.20",
    "rounding_adjustment_aud": "0.00",
    "prices_final": true
  },
  "ended_at": "2026-10-02T02:30:00Z"
}
```

The service derives payer, recipient and direction from the signed amount and binding roles; callers cannot provide an arbitrary destination or override direction. It validates the numeric summary but does not recalculate interval tariffs. The ledger digest identifies the evidence; it does not prove the physical meter was correct.

Return `201` on initial creation and `200` for an identical repeat. Return `409` for the same settlement ID with different immutable content. Enforce uniqueness for the session and ledger revision as well as the settlement ID, and prohibit any second active or paid settlement for the same original session.

### Prepared response

```json
{
  "settlement_id": "7dd9a349-8889-4f80-976b-b3ebd51b80b4",
  "state": "prepared",
  "direction": "operator_to_driver",
  "payer_binding_id": "operator-demo-01",
  "recipient_binding_id": "driver-demo-01",
  "currency": "AUD",
  "net_amount_minor": -30,
  "amount_sats": 3000,
  "fee_policy": "payer_additional",
  "quote": {
    "quote_id": "demo-quote-001",
    "sats_per_aud": "10000",
    "source": "synthetic_demo_fixed_rate",
    "quoted_at": "2026-10-02T02:31:00Z",
    "expires_at": "2026-10-02T02:36:00Z",
    "rounding": "ROUND_HALF_UP"
  },
  "mode": "mock",
  "txid": null
}
```

### Request payment and read status

```http
POST /v1/settlements/7dd9a349-8889-4f80-976b-b3ebd51b80b4/request-payment
Content-Type: application/json

{"quote_id":"demo-quote-001"}
```

The server returns `202` for a new approval request or `200` for the existing request/status. Approval must bind settlement ID, quote ID, network, recipient, exact amount, fee policy and expiry. A generic wallet-login signature is not payment approval.

```json
{
  "settlement_id": "7dd9a349-8889-4f80-976b-b3ebd51b80b4",
  "state": "awaiting_approval",
  "required_action": "payer_approval",
  "approval_request_id": "approval-001",
  "approval_url": null,
  "mode": "mock"
}
```

The live adapter may supply a short-lived approval URL hosted by the service. HA should not persist bearer-like approval URLs in widely visible state attributes. The approval screen must display the immutable payment details fetched from the service, not trust URL query amounts.

For a replacement quote, `POST /quotes` takes `{"replaces_quote_id":"demo-quote-001"}` and returns the new quote with the settlement in `prepared`. Repeating the same replacement request returns that same new quote. Serialize replacement and submission under the same settlement lock; reject replacement once submission has started or an outcome is uncertain. Any older approval becomes invalid. The HA `prepare_session` action may invoke this operation for an already prepared settlement, but may not change its frozen energy account.

An eventual status response includes:

```json
{
  "settlement_id": "7dd9a349-8889-4f80-976b-b3ebd51b80b4",
  "state": "received",
  "direction": "operator_to_driver",
  "amount_sats": 3000,
  "network_fee_sats": 12,
  "txid": "<verified transaction identifier>",
  "recipient_accepted": true,
  "confirmations": 0,
  "receipt_id": "receipt-001",
  "mode": "live",
  "last_error": null
}
```

The fee above is illustrative, not an estimate. Persist network, output index, wallet action reference, signed transaction/proof references, acceptance time and confirmation evidence alongside the public receipt.

### States and failures

Normal flow:

```text
prepared → awaiting_approval → submitting → broadcast → received → confirmed
```

`received` means the intended recipient accepted the verified payment under the chosen policy; `confirmed` additionally means the configured chain-confirmation threshold has been met. Neither status should be inferred from the browser closing or from an HTTP success alone.

| State | Meaning and action |
|---|---|
| `no_payment_due` | Zero rounded account; complete without a transaction. |
| `below_minimum` | Nonzero account cannot be paid at the configured minimum; retain as unsettled. |
| `declined` / `expired` | No valid current approval. Leave the account outstanding. |
| `failed` | Known pre-broadcast failure, such as insufficient funds. A new attempt requires review. |
| `reconciling` | Submission outcome is unknown. Query the stored wallet action/transaction; do not create another payment. |
| `needs_review` | Invalid evidence, conflicting transaction, recipient mismatch or irreconcilable status. Stop automated settlement. |

Use structured API errors: `{"code":"QUOTE_EXPIRED","message":"Prepare a new quote before requesting payment.","retryable":false}`. Use `400` for malformed requests, `401/403` for authentication/permissions, `404` for unknown records, `409` for conflicts, `422` for invalid business data and `503` for a temporarily unavailable backend.

## Wallet adapter and duplicate protection

The service needs a small internal adapter: inspect capabilities, prepare an approval request, submit an approved payment, reconcile an existing action and deliver/verify the recipient receipt. The adapter must distinguish driver-device signing from operator-local signing.

BRC-29 describes recipient-derived payment outputs, payment creation through `createAction`, and delivery of Atomic BEEF plus derivation/remittance information for recipient `internalizeAction`; a transaction ID alone is not the complete wallet-receipt exchange ([BRC-29 payment protocol](https://bsv.brc.dev/payments/0029)).

The candidate operator server documents `createAction`, deferred `signAction`, `internalizeAction` and transaction-history endpoints, but its README does not establish an end-to-end driver approval flow or this service's exactly-once behaviour ([wallet server documentation](https://github.com/Calhooon/bsv-wallet-cli)).

Required protections:

- **Durable intent:** commit settlement, approval and attempt records before external submission.
- **Single active attempt:** lock a settlement in the database, not just in an in-memory coroutine.
- **Persist before broadcast where supported:** retain the wallet action reference and signed transaction before broadcasting. Prefer a tested deferred-signing flow.
- **Ambiguous outcome:** if the backend can broadcast before returning a durable reference, a timeout enters `reconciling`. Do not automatically replay `createAction`.
- **Safe retries:** repeat status queries and re-deliver the same receipt or signed transaction when appropriate; never recreate a payment merely because delivery acknowledgement was lost.
- **Replay protection:** repeated recipient acknowledgement must resolve to the same transaction/output, not a new credit.
- **Restart recovery:** HA reloads its session-to-settlement mapping; the service reconciles nonterminal records before accepting new attempts.
- **Corrections:** after payment, never mutate the original amount. A future correction would require a separately approved adjustment referencing the original; automated corrections are outside this POC.

This design aims to prevent duplicate payments. It does not claim atomic exactly-once execution across the service, two wallets and the blockchain.

## Build sequence and acceptance tests

### Smallest build

1. Implement the durable HA ledger and mock settlement service with the endpoints above.
2. Replay one debit session, one credit session and one zero-balance session using synthetic rates.
3. Add the operator wallet backend and prove a supervised credit to the driver wallet, including recipient receipt handling.
4. Add driver-device approval and prove payment back to the operator.
5. Connect actual OCPP meter events and dynamic tariff intervals after verifying their mapping and data quality.

Use local service polling and wallet-supported outbound receipt delivery first. Do not add a reverse proxy, public webhook infrastructure, hosted identity lookup, app budget permissions or unattended payments merely to complete this demonstration.

### Acceptance checklist

| Test | Required result |
|---|---|
| Debit: import 2 kWh at AUD0.30/kWh | AUD0.60 driver payment; 6,000 sats at the synthetic test rate. |
| Credit: import 3 kWh at AUD0.30; export 2 kWh at AUD0.60 | AUD0.30 operator payment; 3,000 sats at the synthetic test rate. |
| Negative prices | Signed interval arithmetic selects the correct final payer. |
| Price changes mid-session | Each measured interval uses its applicable tariff, not the end-of-session price. |
| Equal debits and credits | `no_payment_due`; no wallet submission. |
| Duplicate session-end event and repeated HTTP request | Same settlement ID and at most one approved transaction. |
| Crash/timeout during broadcast | `reconciling`; no automatic second payment. |
| Recipient unavailable after broadcast | Preserve the original transaction and retry receipt delivery only. |
| Declined approval or insufficient payer funds | Account remains unpaid with a specific status. |
| Quote expires | No submission until a replacement quote is approved. |
| Missing export meter, reset counter or incomplete tariff | `needs_review`; no guessed settlement. |
| Wrong network, wallet binding or payment output | Reject; no paid status. |
| HA/service restart | Restore outstanding ledger, approvals and payment evidence. |
| Mock execution | Clearly marked simulated result; no real-looking transaction receipt. |

### Decisions to validate before live implementation

Confirm the exact driver wallet and supported approval/receipt mechanism; the deployed OCPP directional meters; the final tariff source; the live conversion policy; and the wallet backend's recovery behaviour after uncertain submission. These are compatibility checks, not reasons to reintroduce a budget process.

**Recommended first milestone:** demonstrate a correctly calculated operator-to-driver credit, then the reverse payment, with one receipt linked to each OCPP session and no funds committed before final payer approval.
