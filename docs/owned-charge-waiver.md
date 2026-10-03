# Audited waiver of an existing driver charge

This is a terminal charge close-out, not a payment, refund or recovery release. It preserves the original issued request or wallet attempt and prevents further collection for the account.

## Scope and safeguards

- An authenticated Home Assistant administrator must use both services.
- Select exactly one issued manual request (`review_id`) or pre-permit collection (`budget_id`).
- Only a positive, unchanged retained account payable to this operator is eligible.
- Manual records must have an issued request and no allocated payment receipt.
- Collections must be `ready`, `wallet_attempt_reserved` or `recovery_ready`, with no signing permit, draft hash, signed bytes or transaction ID.
- Competing owners, automatic-credit records, changed accounts and already closed accounts are refused.
- The prepare response contains a hash of the current account, attempt and associated invitations. Execution recomputes this hash under the coordinator lock.
- Associated charge invitations become `charge_waived`. Historical receiving registrations remain valid for other sessions; this is not revocation of the driver.
- Originals, prior invitation states, reason, actor and time remain in the checkpointed ledger. Ownership indices remain reserved.
- A split or failed checkpoint save keeps the wallet fail-closed even after the in-memory rollback.

## Money already received

An optional `received_txid` and `received_output_index` identify an existing incoming output. The service checks the raw transaction hash, exact output amount and operator destination, requires provider confirmations, and rejects an output already allocated elsewhere or dated before the request.

The charge can then be waived while the receipt remains `received_unallocated`. The output is reserved against reuse, and the separate receipt records `refund_authorised: false`. This does not mark the charge paid, erase the receipt, return funds, or determine how the unallocated funds should ultimately be treated.

Provider confirmation is not independent SPV verification. Absence of a server signing permit does not prove a driver cancelled an external draft or that a late external payment cannot arrive.

## Administrator service sequence

1. Call `bsv_settlement.prepare_existing_charge_waiver` with the operator `config_entry_id`, exactly one target ID, and optional receipt transaction/output.
2. Review the returned account, amount, prior state, receipt and `review_hash`.
3. Call `bsv_settlement.waive_existing_charge` with the same target and receipt fields, plus:
   - `expected_review_hash` and `expected_amount_sats` from the review.
   - `reason`: a documented explanation, 8 to 300 characters.
   - `confirm_waive_charge: true`.
   - `confirm_no_refund: true`.
   - `confirm_external_payments_need_separate_accounting: true`.
   - `confirm_received_funds_unallocated: true` when a receipt is supplied.
4. Read the closure, original target and separate received-funds evidence. Do not repeat after a lost response until the saved state has been checked.

These are guarded administrator services, not a new one-click dashboard waiver control. Deployment does not itself waive any account.

## Interface and validation

The operator settlement list shows the waived amount, with unallocated funds and no-refund wording when applicable. Historical diagnostic evidence must not create a current “held for review” warning. The private driver page shows a terminal waived state and hides approval, pairing and collection retry controls.

QA inventory:

| Requirement | Check |
|---|---|
| No payment or refund | Fictional-chain tests assert no network writes |
| Preserve money received | Exact-output validation, allocated-output rejection, persistence and rollback tests |
| Preserve held attempt | Prior fields retained; signing evidence blocks close-out |
| Administrator-only action | HA service tests reject absent and non-admin contexts |
| No stale execution | Account, amount, ownership and review-hash changes reject |
| Old private link safe | HTTP read succeeds; approval, pairing, signing and reporting fail |
| No driver-wide revocation | Other-session receiving registration remains valid |
| Honest operator UI | Fictional 76 sat unallocated and 89 sat waived scenarios |
| Honest driver UI | Waived badge and no collection controls at desktop/mobile sizes |
| Regression checks | Zero balance remains “no payment due”; non-waived held attempt still warns |

No live ledger mutation, wallet signing, broadcast or refund is part of this fixture QA.

Validation on this branch: 440 Python tests, 30 operator JavaScript tests and 60 driver JavaScript tests pass. Bundles are rebuilt from the committed source. Browser checks cover both waived scenarios, the retained non-waived hold warning, overview status, mobile layout, and hidden driver payment controls. The fictional waived driver produced zero claims, drafts, signatures or reports.
