# Verification results

Verified in the sandbox and subsequently in a Home Assistant OS deployment on 2 October 2026. No test contacted a real wallet or blockchain endpoint, and no test operated a charger.

## Automated tests

`python -m pytest -q tests` completed with **27 passing tests** for v0.1.2 against Python 3.14.3, Home Assistant 2026.9.4 and FastAPI 0.142.2. The original v0.1.0 suite contained 23 tests; three distribution checks were added in v0.1.1 for HACS metadata, manifest requirements, branding and translations, followed by a licence check in v0.1.2.

Coverage includes:

- Positive, negative and zero settlement directions.
- Immutable requests, session uniqueness and repeated approval.
- Concurrent approval returning a single persisted receipt.
- Distinct API/approval permissions and declined payments.
- Incorrect arithmetic, unsupported wallet binding and oversized request rejection.
- Service recreation using the same database, retaining the same receipt.
- Quote expiry, replacement and invalidation of stale approval.
- Explicitly simulated confirmation without a fabricated txid.
- Negative prices, interval gaps/overlaps, incomplete prices, timezone and invalid-decimal rejection.
- Multiple tariffs and final-account rounding.
- Actual Home Assistant action registration, durable Store reload, sensor values and configuration form construction.

Two upstream deprecation warnings were reported, one from Home Assistant's HTTP application class and one from Starlette's test client. Neither caused a test failure.

## Real HTTP smoke test

Started the mock with Uvicorn on loopback port 8091 and ran `scripts/mock_cli.py demo` over HTTP. This separate smoke run used the sandbox's FastAPI 0.141.0 and Pydantic 2.12.5.

| Case | Result |
|---|---|
| AUD0.60 debit | `driver_to_operator`, 6,000 synthetic sats, `mock_received` |
| AUD0.30 credit | `operator_to_driver`, 3,000 synthetic sats, `mock_received` |
| Zero account | 0 sats, `no_payment_due`, no receipt |

Both nonzero cases returned `MOCK-` receipts and null txids. No actual BSV transaction was created.

## Boundaries of verification

The standalone Docker/Compose recipe has not been built in the sandbox. The separate Home Assistant OS add-on image was built and exercised on its target system as described below. Automatic OCPP capture, dynamic tariff ingestion, live wallet approval, live payments and blockchain recovery are not implemented or tested.

The public repository includes an official HACS validation workflow. Its remote outcome is recorded in GitHub Actions; local metadata tests alone are not evidence of default HACS catalogue acceptance or an end-user installation test.

## Installed Home Assistant OS verification

Installed the integration through HACS at v0.1.2 and built the separate BSV Wallet Mock add-on at 0.1.0 on an amd64 Home Assistant OS 18.3 system running HA 2026.10.0b0. The integration's official configuration flow completed and its config entry reached `loaded` without restarting Home Assistant.

The installed HA actions, rather than only the standalone CLI, produced:

| Synthetic session | HA account | Service outcome |
|---|---:|---|
| Driver debit | AUD0.60 | 6,000 synthetic sats, `mock_received` |
| Zero balance | AUD0.00 | `no_payment_due`, no receipt |
| Driver credit | AUD-0.30 | 3,000 synthetic sats, `mock_received` |

Repeating each nonzero approval returned the same receipt. Restarting only the new mock add-on preserved the credit record and receipt. The five HA sensors showed 3 kWh import, 2 kWh export, AUD-0.30, `mock_received` and 3,000 synthetic sats for the final test.

The add-on was verified running with automatic start, protection enabled, no host port mapping, no privileged capabilities, and no Supervisor or Home Assistant API permission. Tokens were generated for mock use and were not committed or printed. No git-managed HA configuration, charger control or existing automation was changed.

HACS can retain its generic restart-required notice after download even when this newly added integration has loaded successfully. A complete HA restart was not required for this verification.
