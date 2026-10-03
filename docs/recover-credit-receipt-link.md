# Recover the original private credit link

Use the administrator-only `bsv_settlement.get_credit_receipt_link` action when a
driver has lost the page used to register the receiving wallet for an already
confirmed operator credit. It supports original single-session approvals and
ongoing or linked recovery credits routed to those approvals.

## Administrator procedure

1. Select the exact credit in `automatic_credit.payments` or
   `ongoing_credit.sessions` on the operator wallet status entity.
2. Supply the mainnet `config_entry_id`, exact stored `credit_id` (the payment's
   `budget_id` for a direct credit), `expected_txid`, and
   `expected_recipient_address`.
3. Set `confirm_private_link_disclosure: true` and request the service response.
4. Privately give `driver_url` to the original receiving driver. They open it in
   their BSV wallet and receive the existing confirmed receipt. An ongoing-credit
   page can list several credits belonging to that same receiving registration.

The URL is assembled only from Home Assistant's configured public HTTPS external
URL. There is no caller-supplied redirect destination. The capability remains in
the URL fragment, not a query parameter.

## Guardrails

- Requires an authenticated Home Assistant administrator, not an ordinary user,
  context-free automation or public driver action.
- Requires exact stored confirmation evidence, TXID and recipient matching.
  It does not refresh chain status; `chain_checked_at` shows the evidence age.
  Existing receipt retrieval still checks confirmation before receipt import.
- Verifies original invitation, driver consent signature, receiving-key proof and
  route binding. Never substitutes the latest registration.
- Recomputes only the established deterministic token and checks it against the
  original stored hash. A missing legacy scheme or changed token hash fails closed.
- Rejects multi-session/weekly approvals and unsettled original driver collections.
  Unrelated ongoing credits also require terminal evidence for the original account.
- Does not sign a payment, broadcast, refresh/tick settlement, write storage,
  rotate a capability, renew consent, reassign funds, release a hold or create an
  invitation. Disabled payments and expired/revoked consent remain unchanged.

This recovers the **original session capability**, not a new receipt-only token.
Its original scope and existing permission checks remain in force. Keep the URL
out of public issues, dashboards, persistent notifications, screenshots and logs.
Home Assistant service responses and client-side traces must be treated as private.
The integration does not add the token to entities, public status or storage.

## Receipt acceptance

Recovering a link proves neither wallet import nor receipt acceptance. The separate
wallet-receipt-reporting change persists a wallet-signed acceptance report after
`internalizeAction` succeeds. This recovery change can be merged independently.

No real TXIDs, driver identities, addresses, private URLs or wallet keys are used
in the test fixtures or these public instructions.
