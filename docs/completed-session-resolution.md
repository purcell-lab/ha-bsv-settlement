# Resolve a completed session

This flow separates a completed energy account from its payment outcome. It does not alter the metered session, erase a warning, transfer funds when an operator creates a link, or treat a waiver as money received.

## Operator workflow

Open Payments, then **Resolve a completed session**. Select a recent completed session or enter its retained session ID and select **Review closure options**.

- **Request driver consent:** review the frozen energy account, enter a reason and confirm the account. If a supported provisional warning is present, explicitly accept it. Check the total debit limit, fee ceiling and expiry. Explicitly confirm replacement of any unsigned invitation. The new private link and QR require a fresh driver signature.
- **Waive driver payment:** review the same account and confirm the waiver with a reason. The system revokes its pending unsigned invitation and retains an audited `waived` outcome. It does not label the charge paid.
- **Close zero balance:** record the review and reason without creating a wallet payment.
- **Existing settlement:** active signed approvals, claimed attempts, submitted transactions and other settlement ownership must use existing guarded reconciliation. These controls cannot clear a hold.

An administrator must perform each action. A review fingerprint binds the account, conversion and pending invitation. If any changes, the operator must review again. Failed persistence rolls back the in-memory change.

## Provisional metering policy

Only `import:energy_without_matching_state` and `export:energy_without_matching_state` can be accepted with a documented explanation. These flags represent an energy-counter and running-state mismatch; accepting them does not independently validate the meter.

The warning remains in the frozen account and is disclosed with the operator's reason before the driver signs. Existing benign estimated interval allocation and non-final-bill notices remain in the account. Missing or estimated prices, missing baseline, unavailable amounts, unknown running state, and possible duplicate payments remain blocking.

Negative accounts are money owed to the driver. This flow cannot waive them; use the operator-credit workflow. A waiver is terminal in this interface, with no automatic debit or credit subsequently allowed for that account.

## Driver workflow

Open the new private link in BSV Browser, or use the existing wallet pairing flow. The page shows:

- Completed transaction ID and imported/exported kWh.
- Frozen net AUD amount, payment in sat and average net AUD per total kWh, excluding the network fee.
- Metering warning and operator explanation.
- Total debit limit, fee ceiling and expiry.
- Explicit notice that approval can start collection immediately.

Live Amber prices do not gate consent for an already frozen historical account. The new signature covers the invitation containing that account. The later signed quote must match it exactly. The existing wallet claim, draft validation, fee limits and no-duplicate guards still apply.

This approval does not start another session, extend an old approval, register a receiving key, or change ongoing-credit recipients. Keep the page and wallet available until collection completes. Consent is not a funds reservation or payment guarantee.

## Services

`prepare_session_closure` returns a read-only review. Supply wallet `config_entry_id`, `proxy_config_entry_id`, completed `session_id`, and `conversion_rate_entity`.

`request_closed_session_consent` also requires the current `expected_review_hash`, reason and `confirm_account_review`. Supported warnings require `confirm_provisional_metering`; an existing pending invitation requires `confirm_replace_pending`. Standard invitation limit/contact fields apply.

`waive_session_charge` uses the same reviewed-account fields and requires `confirm_no_payment`. The stored audit includes account, warnings, reason, actor, timestamp and review hash. Public dashboard attributes omit the actor.

## QA inventory

- Review, required reason and explicit warning acceptance: backend negative tests and operator browser controls.
- Consent issuance and QR: fictional operator fixture, including confirm/cancel and replacement.
- Fresh signed closed account and unchanged quote: Python and SDK JavaScript tests.
- Driver disclosure, disabled live-price dependency, no credit registration: fictional signed driver fixture.
- Waiver/zero closure, restart persistence, rollback and replay guards: Python tests and operator states.
- Held/paid/credit/unknown-data refusals: backend negative tests; no live financial action.
- Form retention across refresh; mobile 375px, desktop 1280px and dark appearance: browser QA.
- Regression: full Python, driver SDK and operator test suites plus reproducible bundle build.

No live consent, waiver, payment, merge, HACS installation or restart is part of this staged change. Real BSV Browser end-to-end collection remains a supervised post-deployment test, with separate payment authority.

### Validation results

Local regression passed: 401 Python tests, 59 driver SDK tests and 27 operator tests, 487 total. Compilation, service-description YAML parsing and whitespace checks passed.

Fictional browser checks exercised missing-reason rejection, consent confirmation and cancellation, private QR display, zero closure, waiver and its non-payment ledger label. Desktop 1280px, mobile 375px and dark-mode views were inspected; no horizontal overflow or browser script errors were observed. Form text survived a status refresh.

The signed driver fixture showed the completed account, human-readable metering warning and immediate-collection disclosure with live prices unavailable. A mock wallet signed fresh consent, without receiving-key registration or any transaction draft, signing or broadcast. It deliberately stops before collection; automated SDK/backend tests cover quote binding and payment guards.
