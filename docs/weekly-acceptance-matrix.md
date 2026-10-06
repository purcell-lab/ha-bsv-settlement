# Weekly settlement acceptance matrix

R3 of [#91](https://github.com/purcell-lab/ha-bsv-settlement/issues/91).
This matrix records test coverage and uncompleted live gates, not an authority
to move funds, reconnect wallets or control a charger.

## Automated contracts

Existing regression owners are retained rather than duplicated as a new suite.
Run `python scripts/validate.py all` using the pinned development environment;
HACS is a separate CI check. Quick mode is not release evidence.

| Outcome | Regression owner / exact example | Native or independent gate |
|---|---|---|
| Fresh weekly scope | `test_weekly_mandate.py::test_fresh_seven_day_signature_and_no_existing_approval_upgrade` | Installed wallet displays 1,000 sat / seven days / fees and signs the exact scope |
| Include named current session | `test_current_weekly.py::test_open_current_included_without_backdating_other_accounts` | Correct vehicle/session ownership, no automatic reassignment |
| Separate collections, shared allowance | `test_weekly_mandate.py::test_two_sessions_share_total_including_actual_fees_and_restart` | Two distinct closed-session payments and correct remaining allowance |
| Uncertainty cannot replenish budget | `test_weekly_mandate.py::test_uncertainty_or_waiver_does_not_refill_allowance` | UI remains honest after loss of connection |
| Expiry/revocation after signing | `test_weekly_mandate.py::test_stop_after_signing_preserves_hold_and_no_new_broadcast` | Wallet refusal/late response, no new transfer |
| Expired consent can reconcile existing payment | `test_weekly_mandate.py::test_expired_parent_can_reconcile_existing_transaction_without_repayment` | Same transaction ID, no renewed spending scope |
| No double reservation | `test_weekly_mandate.py::test_http_concurrent_sessions_cannot_double_reserve_allowance` | Multi-device native acceptance remains separately gated |
| Uncertain collection | `test_collection.py::test_uncertain_broadcast_only_reconciles_never_resubmits` | Input/spend evidence for historical uncertain attempts, not provider absence alone |
| Credit restart safety | `test_credit_interruption_matrix.py::test_interrupted_credit_reloads_without_duplicate_external_effect` | Native receipt acceptance and isolated protected restore |
| Zero/tiny accounts | `test_collection.py::test_negative_zero_and_over_limit_accounts_do_not_collect` | Review rounding and zero/tiny-account policy against independent reference accounts |
| Original-wallet receipt ownership | `test_receipt_ack.py::test_wrong_signer_invalid_signature_and_cross_registration` | Original receiving identity imports existing receipt; no resend |
| Incoming receipt acknowledgement | `test_receipt_ack.py::test_persist_idempotent_reload_and_no_payment_effect` | Record actual wallet acceptance independently of provider confirmation |
| Retained diagnostics | `test_collection_diagnostics.py` | Administrator-only deployed smoke test, no ledger writes or network effects |
| Outgoing local wallet state | `frontend/driver/outgoing-evidence.test.js` | R2 native status repair remains NOT IMPLEMENTED and inactive |
| Conflicting and duplicate history | `frontend/settlement-evidence.test.js` | Mobile/desktop fixture QA; later read-only live inspection |

## R3 display contract

The operator projection presents one session summary with expandable retained
evidence. Confirmed payments are not replaced by older blocked routes or cancelled
unsigned reviews. Distinct transaction IDs and contradictory financial fields
remain explicit conflicts, with no invented combined amount.

For the same transaction, a later timestamped uncertain observation supersedes
earlier confirmation. Conflicting states with missing or tied timestamps remain
a warning instead of selecting the most optimistic state. Receipt acknowledgement
is carried only within a compatible same-transaction group and is never invented.

The projection does not alter the ledger or driver ownership. Mixed-direction
conflicts appear in both recipient views rather than disappearing. Conflict rows
offer provider checking only, not waiver, fresh consent or collection. The original
wallet-signed portal history is not rewritten by this operator display change.

## Browser QA inventory

The separate automatic-credit list is labelled and collapsed as historical
audit records, rather than competing with the consolidated current outcome.
Authenticated backend display rows include their saved check timestamps,
confirmation count and output index; no new provider read is initiated.

Preserve the existing design. Fixture-only scenarios:

- Confirmed credit with an old blocked route and cancelled review: one payment,
  correct amount, expandable audit records and no stale error.
- Two different transaction IDs: visible conflict with retained references and
  no actionable new payment.
- Same transaction, newer uncertain observation: warning, not confirmed.
- Expand/collapse retained evidence using pointer and keyboard; select normal,
  conflict and confirmation-loss scenarios, then return to normal.
- Desktop 1280px and mobile 375px, light/dark, no page-level horizontal overflow.
  Table-local horizontal scrolling remains intentional.

No live wallet, provider, HA or charging commands are used by the preview.

## Rollout boundaries

R1/R2/R3 remain draft PRs. Before deployment: review exact combined commits,
passing full CI, current sessions/in-flight attempts, a fresh protected backup,
and separate merge/install/restart approval. After deployment, first check
read-only states and absence of side effects. Any native wallet mutation or
real-value acceptance test requires separately reviewed scope.

Do not close #22 or pilot readiness from automated results alone. Independent
meter/tariff accounts, protected restore, native-device evidence and security/
commercial review remain open.
