# Monthly charging: migration, recovery and release (S5)

Staged S5 of the [approved workflow](https://github.com/purcell-lab/ha-bsv-settlement/issues/79),
stacked on S4. **This document authorises nothing.** Merge, deployment, restart,
activation, wallet-permission increases and any real-value pilot each need a
separate, explicit owner approval recorded on #79.

## Release status

| Layer | State |
|---|---|
| S1 allowance model | Implemented, offline tests |
| S2 authority, ledger, ownership | Implemented, internal only |
| S3 portal transport, orchestrator | Implemented, disabled without activation record |
| S4 Station / My charging / History | Implemented; monthly action disabled while runtime is off; public terms remain visible |
| Production adapters | **Not implemented** (period policy evidence, native grant, session ownership, closed-session record, spending reconciler) |
| Monthly credit/zero routes for monthly-bound sessions | **Not implemented** |
| Native wallet/device evidence | **None recorded** |
| Activation | **Not possible** until the rows above are complete |

## Holistic review corrections

F1-F6 are addressed within S3/S4, with detailed regression mapping in
[station review corrections](qa/station-review-corrections.md).

- **F1:** readiness requires session adapters, fresh executor health, positive
  app/native allowance and a stable ledger revision. The UI also requires a
  recently verified connected signer; login alone is never automatic-ready.
- **F2:** retained monthly bindings and frozen accounts appear in owner-filtered
  history even while activation is off. Accounting commit is not chain finality.
- **F3:** confirmation is visible and focused when approval starts from any tab.
- **F4:** incomplete setup remains resumable without a new mandate or allowance
  reset. Unsupported production setup adapters are explicit operator work.
- **F5:** OCPP comes from the guarded observer, not the Sigenergy running state.
- **F6:** live and expanded account views apply the same stale/unknown rules.

These corrections do not complete the unimplemented production adapters or
monthly credit/zero settlement routes. No native acceptance gate is satisfied
by these fictional-wallet tests, and no new live authority is enabled.

## Integrated failure and migration matrix

Labels: **offline** = automated test with fictional wallets/adapters;
**mock** = offline browser preview; **native** = named real wallet and device.
Native rows stay *pending* until evidence is recorded in the register below.

| # | Case | Evidence | Label | Status |
|---|---|---|---|---|
| 1 | Fresh, returning, locked wallet, expired portal login | `test_case1_expired_login_blocks_monthly_and_returning_driver_sees_authority`; `monthly-wallet.test.js` fresh/returning/locked | offline, mock | Covered offline · native pending |
| 2 | Missing API, declined prompt, monthly grant unsupported | `monthly-wallet.test.js` missing API/declined; `test_honest_readiness_without_native_grant_evidence` | offline | Covered offline · native pending |
| 3 | Wrong network/identity, expired pairing, browser closed before/during signing | `test_case3_browser_closed_after_signing_replays_safely_and_expired_request_refused`; orchestrator testnet/identity tests; `test_expired_or_replayed_challenge_cannot_authorise_other_id` | offline | Covered offline · expired *pairing* native pending |
| 4 | Two stations / two tabs competing | `test_case4_two_tabs_racing_one_wins_and_ledger_stays_consistent`; `test_concurrent_reservations_share_revision_and_total_limit`; `test_other_driver_cannot_see_or_accept_and_stale_tab_conflicts` | offline | Covered offline |
| 5 | Fees at limit, booked overrun, insufficient funds | `test_case5_*`; `test_total_fee_inclusive_cap`; `test_reconciliation_is_idempotent_and_retains_actual_overrun` | offline | Covered offline · insufficient *wallet balance* native pending |
| 6 | Negative prices, both directions, zero and stale estimates | `test_case6_credit_and_zero_sessions_never_enter_the_debit_route`; `station.test.js` stale/missing; #78 tests | offline, mock | Covered offline |
| 7 | Month boundary before/during signing, after uncertain submission | `test_case7_*`; `test_month_boundary_does_not_sign_or_move_uncertain_attempt`; `test_wallet_adapter_await_crossing_month_boundary_is_rechecked` | offline | Covered offline · wallet's own booking month native pending |
| 8 | Cancellation before claim, between claim and signing, after submission | `test_case8_cancellation_after_submission_keeps_the_attempt_recoverable`; `test_cancellation_is_signed_stops_new_actions_preserves_reconcile`; `test_cancel_stops_new_actions_not_reconciliation` | offline | Covered offline · native revocation pending |
| 9 | Old weekly consent, new monthly consent, manual collection collide | `test_legacy_index_prevents_monthly_binding`; `test_monthly_binding_excludes_legacy_closure_and_another_identity`; `test_wrong_key_and_legacy_protocol_rejected` | offline | Covered offline |
| 10 | HA restart at every persisted transition; replay of identical requests | `test_case10_restart_after_each_transition_restores_identical_status`; `test_real_ha_checkpointed_store_restart_and_split_write`; `test_signed_monthly_terms_and_replay_do_not_reset_ledger` | offline | Covered offline |
| 11 | Provider-confirmed, wallet-not-accepted credit; original identity reconnect imports all receipts | `test_history_pagination_is_owner_filtered_not_limited_to_last_twenty_routes`; `test_receipt_reporting_requires_both_owner_login_and_original_wallet_signature`; `station.test.js` separate states | offline | Covered offline · native import pending |
| 12 | Unknown broadcast, reorg/provider outage, duplicate acknowledgement | `test_one_wallet_operation_and_payment_evidence_cannot_cover_two_sessions`; `test_second_reserved_attempt_cannot_sign_when_first_becomes_uncertain` | offline | Partially covered: monthly spending reconciler not implemented |
| 13 | Public QR, private link, copying payload, mobile and keyboard | `docs/qa/station-first-interface.md` (57/57) | mock | Covered in preview · screen reader and native device pending |
| 14 | Disable monthly without deleting holds or re-enabling legacy | `test_case14_deactivation_keeps_holds_and_never_reenables_legacy_routes` | offline | Covered offline |

## Native evidence register

Fill one row per claimed capability. Offline fixtures (`wallet-fixtures.js`) are
not evidence. No row is complete today.

| Wallet | Version | Device / OS | Capability | Result | Date | Evidence link | Reviewer |
|---|---|---|---|---|---|---|---|
| Metanet Desktop | — | — | identity, signature under `[2, "ev monthly spending"]` | pending | | | |
| Metanet Desktop | — | — | monthly spending permission: request, query, revoke, timezone, booking event, fee inclusion | pending | | | |
| BSV Browser | — | — | same as above, plus pairing expiry | pending | | | |
| each | — | — | receipt import (`internalizeAction`) for the original identity | pending | | | |

## Migration

- **Storage:** monthly state lives under the operator store key `monthly_authorities`
  (`monthly-authorities-v1`) inside the existing checkpointed wallet store. Nothing
  is written until a challenge is issued under an activation record.
- **No automatic migration.** Legacy session/weekly consent, invitations,
  recipients and old debts are never converted into monthly authority. A driver
  with legacy consent authorises monthly charging explicitly. Accounts already
  owned by a legacy route can never be bound to monthly authority.
- **Schema changes** must add a new schema identifier and an explicit, tested
  converter. Unknown or corrupt state fails closed and blocks monthly actions;
  it is never reset to an empty allowance.
- **Expired requests:** unused challenges past their 10-minute window are pruned
  before a new challenge is issued. Used challenges are retained as consent evidence.

## Rollback

1. **Preferred: disable, don't downgrade.** Remove the activation record. Status
   reports `disabled`; every authority, hold, uncertain attempt and binding is kept;
   legacy routes still refuse monthly-owned accounts (case 14).
2. **Downgrade hazard.** Versions before S2 do not know `monthly_authorities` and
   do not enforce monthly exclusion. **Do not downgrade below S2 while any monthly
   binding exists.** A pre-S2 legacy route could then collect a monthly-owned
   account a second time. If a downgrade is unavoidable, first reconcile and close
   every monthly-bound account and record the decision on #79.
3. **Backups:** take a Home Assistant backup before activation and before each pilot
   step. Restoring an older backup rolls back the ledger coherently; the checkpoint
   cannot detect that (no remote monotonic witness). Reconcile every wallet
   operation recorded after the backup before re-enabling.

## Reproducible builds

CI (`Driver wallet consent tests and reproducible bundle`) rebuilds
`app.bundle.js` with `npm ci && npm run build` and fails on any difference.
Locally: `cd frontend/driver && npm ci && npm test && npm run build && git diff --exit-code -- ../../custom_components/bsv_settlement/frontend/driver/app.bundle.js`.
Copy `index.html` and `style.css` to `custom_components/bsv_settlement/frontend/driver/` when they change.

## Activation runbook (requires separate approval at each gate)

1. **Origin isolation:** decide dedicated origin or reviewed shared-origin scope; record on #79.
2. **Period policy:** for each supported wallet/version, a reviewed `WalletPeriodPolicy`
   with real timezone, booking event and fee basis, citing native evidence.
3. **Adapters:** implement and review native grant, session ownership (connector to
   session, wrong-driver rejection), closed-session record and the spending
   reconciler (exactly once per session; repeated `wallet_pending` reads never
   re-invoke the wallet; commit only from verified wallet evidence). Supply a
   read-only executor/reconciler health adapter returning fresh observations, not
   a static readiness flag. Install and verify receiving-registration and native
   permission setup adapters before claiming one-action setup is complete.
4. **Credit/zero routes** for monthly-bound sessions, with immutable receiving metadata.
5. **Native evidence register** complete for every capability the UI claims.
6. **Activation record:** construct `MonthlyPortal(service, station_ids, policy_id)` in
   setup behind an explicit option, for one station.
7. **Pilot** (separately approved): one station, one driver wallet funded with a
   small balance, reviewed recipients, amounts and fees; observe each session from
   closure to provider confirmation and wallet receipt; stop on any uncertain
   attempt, overrun, mismatch or unexplained log entry.
8. **Deactivate** (rollback step 1) on any stop condition. Document outcomes on #79.

## #79 acceptance gates

| Gate | State |
|---|---|
| Single entry | Met in preview (S4) |
| Honest readiness | Regression-tested for missing adapters, exhausted allowance, cancellation race and disconnected wallet; native evidence pending |
| Bounded spending | Met offline (S1/S2) |
| No refill | Met offline (S1) |
| Month rollover | Met offline (S1/S2/S5) |
| Correct driver | Met offline (S2/S3) |
| One account | Monthly ownership/HTTP projection and stale/OCPP regressions tested offline; native end-to-end reconciliation pending |
| All receipts | Met offline for existing credits; monthly credit route pending |
| Cancellation | Met offline; native revocation pending |
| Legacy isolation | Met offline (S2/S5) |
| No false finality | Met offline (S4) |
| Native acceptance | **Not met** |
