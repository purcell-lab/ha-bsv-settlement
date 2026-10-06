# Changelog

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
