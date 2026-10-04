# Collection IDs and separate adjustment validation

## Scope

This revision fixes Owner credits routing and adds administrator preparation buttons for separate 5 kWh-equivalent real-value adjustments. It does not deploy the integration, change the live dashboard configuration, execute an adjustment or alter existing payment records.

## Automated checks

- 1,025 Python/Home Assistant tests passed, including 19 new adjustment cases and eight new collection-summary cases.
- 76 operator UI tests passed.
- 96 driver UI tests passed.
- Generated operator and driver bundles rebuilt from source.
- `git diff --check` passed.

The collection tests feed real `MainnetWalletAPI.payment_summary()` output into the JavaScript action selector. They cover expired approvals, weekly child collections and competing newer approvals. Synthetic fixtures contain no live driver IDs, addresses, tokens or transaction data.

Adjustment tests use fictional keys and providers, covering signed receiving registrations, both tariff signs, retry/restart protection, price validity, amount limits, unchanged metering, revoked registration, explicit manual debit, fee-aware operator credit, one broadcast on replay, original-wallet receipt ownership and receipt acknowledgement. No live provider or broadcaster is called.

## Browser QA inventory

- Desktop and mobile: both preparation buttons visible and usable.
- Export preparation: only the prepare service is called; frozen 5 kWh basis, price, direction, recipient and distinct adjustment reference displayed.
- Credit: account and driver checks required; fee preparation remains unsigned; broadcast button disabled until explicit final consent.
- Import preparation: separate manual request with exact amount, address-only QR, address text and copy control; no charging-budget collection call.
- Viewer mode: both adjustment buttons disabled.
- Stale-price error: preparation failure displayed without presenting a new payment.
- Existing Owner credits actions: exact expired-request and held-collection controls remain available.

Browser interactions use the fictional preview API. A preview “broadcast” only records a local JavaScript call; it never reaches a wallet or network payment service.
