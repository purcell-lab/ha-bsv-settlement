# Changelog

## Unreleased: signed portal requests

- `registration_offer`, `debit_authorise` and `debit_failure` now need a per-request wallet signature as well as the sign-in cookie. The signature covers the exact body, the origin, this browser's sign-in and a single-use server nonce (`request_nonce`), under the separate protocol `[2, "ev portal request"]`. Pattern adapted from create-bsv-app's `signed-requests` capability, verified in Python.
- The driver page signs these automatically. Each weekly offer request and each payment permit adds one wallet signature; the payment transaction itself still needs its own wallet approval. Every other money-moving action already carried a wallet proof and is unchanged.
- Refusals return `403` with code `request_signature_required` and do not sign the driver out. An end-to-end test checks that the official TypeScript SDK signature verifies in Python.

## Unreleased: EV app milestone 1 (read-only, hosted by the integration)

- Add `apps/ev-app/client`, a React driver client scaffolded with `create-bsv-app@1.1.2`. Its build is committed to `custom_components/bsv_settlement/frontend/app/` and served by the integration at `/bsv_settlement/app/index.html`, next to the driver page. There is no separate server and no Home Assistant token: the scaffold's Express half was removed.
- The app uses the existing same-origin driver portal API (`/api/bsv_settlement/portal`) for five actions only: `prices`, `challenge`, `login`, `sessions` and `logout`. Wallet sign-in is the portal's existing wallet-signature challenge, signed exactly as the driver page signs it (not BRC-103 `@bsv/auth`, which HA cannot verify). The sign-in uses the portal's existing HttpOnly cookie.
- Read-only: public rates, then the driver's own sessions and payments. Unknown values show "Unavailable", never 0. Provisional amounts are labelled and the session conversion rate is labelled "demonstration rate, not market FX". No registration, pairing, approval, collection, debit, credit, waiver or charger actions.
- The only Python change is one more static path (`/bsv_settlement/app`). The clean-install smoke test now checks that every driver page and app file is served byte-identical. The `EV app (apps/ev-app)` CI job runs the client tests, rebuilds the bundle and fails on any diff.

## Unreleased: wallet-connected 5 kWh adjustments

- New debit-button adjustments enter the authenticated wallet queue with an
  exact signed quote, explicit wallet-native approval and a one-use signing
  permit. They do not consume the weekly charging allowance.
- Credit-button adjustments retain automatic owner-scoped receipt delivery.
  Both directions show the adjustment basis and applied rate in driver history.
- Reuse guarded draft validation and confirmation-only reconciliation. Preserve
  all historical manual requests, uncertain attempts and frozen recipients.

## Unreleased: adjustment receipt identity and settlement status

- Return the original receiving-approval ID as `budget_id` and the adjustment payment ID as `credit_id` in authenticated adjustment receipt responses. Preserve the original payment, receiving address and all cryptographic checks.
- Add a visible receipt-only retry beside paused wallet receipt status; remove the obsolete instruction to sign in again.
- Describe session-collection readiness separately from wallet-native spending permission. A ready indication applies only to eligible covered sessions, never another wallet's historical charge.
- Add authenticated adjustment receipt/acknowledgement and browser remittance regressions. No payment retry, historical reassignment or approval changes.

## Unreleased: automatic wallet setup expiry race

- Set the verified login deadline before exposing the identity or awaiting session history. A slow HA history response no longer lets the one-second expiry watchdog clear a freshly authenticated wallet.
- Reject invalid login lifetimes before publishing identity; retain identity mismatch, cancellation, real expiry and payment-hold protections.
- Add a delayed-history preview and regression tests over the actual portal sign-in function. No wallet permission, budget, recipient or payment changes.

## Unreleased: rapid prototype validation

- Use a focused weekly-settlement Python gate for routine PRs and main pushes, adding changed-area regressions and falling back to full validation for broad or unmapped backend changes.
- Retain every regression test. Full Python validation remains explicit through `validate.py python`, manual CI with `full_regression=true`, and version-tag CI.
- Keep driver/operator tests, reproducible bundles, clean-install smoke tests and HACS checks. Update forward compatibility to HA 2026.10.0b3.
- No runtime integration, ledger, payment, wallet-policy or storage changes.

## Unreleased: mainnet wallet fixes (#102, #113, #114)

- #102: a provider with no evidence for the active payment's txid (e.g. signed, never posted) no longer blanks the operator balance or fails the refresh. The balance is kept, the payment state is unchanged, and `payment_check_error` (`payment_evidence_unavailable` / `payment_evidence_invalid`) appears on wallet status and the balance/status sensors until evidence appears.
- #113: `wallet_status`, `wallet_self_test`, `bind_session`, `add_interval` and `prepare_session` now require an authenticated HA administrator (no-user automation calls are refused), as they write to or expose the mainnet ledger. Self-test signing is unchanged.
- #114: add the administrator-only `purge_removed_backend_stores` action. Given a deleted `mock`/`embedded_testnet` entry's `entry_id` and `confirm: true`, it removes exactly that entry's coordinator, wallet-ledger and operator-key stores via the HA Store API and returns their keys. It refuses (removing nothing) while any config entry has that ID, if any other per-entry store exists, if a store is unrecognised, or unless every present store is positively testnet/mock.

## Unreleased: drop mock and testnet backends from the integration

- Remove the `mock` (HTTP mock wallet service) and `embedded_testnet` backends from the config flow and setup. The backend form defaults to the read-only `sensor_proxy`; the mainnet step keeps all three acknowledgements.
- Existing `mock` / `embedded_testnet` entries now fail with `ConfigEntryError` ("backend was removed … delete this entry"). Their stores are not read, rewritten or deleted; other entries are unaffected; deleting the entry works.
- Remove the mock-only `request_payment` action and the `driver-demo-01` binding. `bind_session`, `add_interval` and `prepare_session` remain for never-paying drafts on the mainnet entry; `wallet_self_test` remains offline on mainnet.
- `embedded.py` is now only the shared base of the mainnet wallet. Mainnet keys, stores, payments and records are unchanged.
- Dashboard migration drops the testnet self-test and mock-results cards from the Diagnostics view, repeat-safely. Remove the mock-only example script.
- Rename the integration and HACS entry to "BSV Settlement". The standalone `wallet_service`, `bsv_wallet_mock` add-on and `scripts/mock_cli.py` are unchanged.

## Unreleased: packaging, clean install and release checklist

- Run the Python/HA suite in CI on HA 2026.9.4 (the declared minimum, same check name) and on 2026.10.0b2.
- Add an offline clean-install smoke test. It copies only the HACS payload into an empty config, sets it up through the config flows in safe modes, then checks entities, actions and frontend paths and unloads.
- Add upgrade/rollback storage tests over fictional fixture stores from v0.1.2 and the earlier main layout. No private key is committed.
- Add `docs/release-checklist.md`: owner roles, pre-flight, verification, rollback and downgrade hazards. Correct the HACS install steps for backend selection.
- No change to integration code, payment or signing behaviour, and no version bump.

## Unreleased: Home Assistant OS deployment

- Add a separately installable BSV Wallet Mock app/add-on with persistent storage and internal-only networking.
- Pin its wallet-service source to the MIT-licensed implementation commit.
- Verify HACS installation, the official integration configuration flow, synthetic debit/credit/zero sessions and persistence across a mock-service restart.
- Leave existing HA configuration and charger controls unchanged.

## 0.1.2: MIT licence

- Add the owner-approved MIT licence for the original code and documentation.
- Clarify that third-party material retains its respective rights.
- Add a licence-presence test and rerun HACS validation.

Mock-only behaviour and the separate wallet-service deployment are unchanged.

## 0.1.1: HACS custom repository scaffolding

- Add root `hacs.json` with Home Assistant 2026.9.4 as the tested minimum.
- Add HACS custom-repository installation and update instructions.
- Add original generic wallet branding at the repository and integration levels.
- Add GitHub workflows for Python/HA tests and official HACS validation.
- Add local distribution metadata tests.
- Publish repository for public access.

The implementation remains mock-only. HACS downloads the HA component, not the separate wallet service. There is no default HACS catalogue listing, real wallet adapter or automatic OCPP mapping.

## 0.1.0: Initial proof of concept

Runnable mock settlement service, HA custom-integration scaffold, durable ledger, synthetic debit/credit/zero examples, tests and design documents. Earlier budget-first concepts are archived and explicitly superseded.
