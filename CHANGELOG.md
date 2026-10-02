# Changelog

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
