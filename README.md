# BSV session settlement: runnable mock and HA scaffold

Version 0.1.2 | Prepared for Mark Purcell | 2 October 2026

**Session-end settlement proof of concept with two separate backends.** Release v0.1.2 provides the original mock service. The development branch also provides an embedded Python SDK operator wallet for **unfunded, offline testnet validation**, without a separate service or CLI. It creates a real operator key locally, but cannot broadcast or move funds. Neither backend implements the budget gate or automatic OCPP capture yet.

**Mainnet development:** a third, separate operator-wallet backend now supports public driver-input dialogs and exact-approval mainnet P2PKH payments. Read the [mainnet safety and payment guide](docs/mainnet-operator-wallet.md) before configuring or funding it. This is an experimental hot wallet, not an audited or complete charging-settlement product.

See [embedded operator-wallet setup and safety boundaries](docs/embedded-operator-wallet.md). No new version or release tag has been created.

## End-to-end concept

![Bidirectional EV wallet settlement concept: BSV budget approval gates session start, OCPP measures imports and exports, Home Assistant applies dynamic prices, and wallets settle the final payment or credit. The budget gate and real wallet connection are planned, not implemented.](docs/images/settlement-infographic.png)

[Open the full-resolution infographic](docs/images/settlement-infographic.png).

**Target design:** retain the BSV budget gate before session start, then meter and price energy dynamically and settle the final payment or credit. Budget authorisation is not a prepayment or a guarantee of available funds. The planned controller must monitor spend and pause or obtain renewed approval before the limit is reached, while preserving charger and grid safety controls.

**Original mock:** the Home Assistant integration, mock service and settlement dashboard exercise synthetic session-end settlement only. The budget gate and automatic OCPP meter feed are not implemented. No backend is wired to `bsv-wallet-cli`; the separate mainnet backend uses the Python SDK and can send real BSV only through its explicit administrator-approved workflow.

**New development milestone:** the standalone embedded backend uses `bsv-sdk==2.4.0`, not `bsv-wallet-cli`. It provides persistent operator identity, real offline signature tests and local settlement drafts. This is not yet a completed live-payment adapter; the infographic's real-payment and budget steps remain target capabilities.

The embedded backend has now been activated on HA 2026.10.0b0, with offline signature/script verification and identity persistence across entry reload verified. See the [deployment validation record](docs/embedded-operator-wallet.md#verified-ha-deployment). Broadcasting remains disabled.

## Design documents

**Session-linked review:** development code also provides a frozen-account
review card, a manually fulfilled driver payment request and separately
approved operator credits. The driver wallet remains external, identity
attestation is manual and no automatic payment is enabled. Read the
[payment request and credit review guide](docs/session-payment-review.md)
before configuring or using it.

**Live read-only session recorder:** the development branch now includes a
separate `sensor_proxy` backend for cumulative charger import/export counters,
Sigen-style running states and historical interval-price sensors. It provides
stable proxy transaction IDs, automatically updating provisional energy/cost
sensors and recorder-assisted restart recovery. It cannot control a charger
or request a wallet payment. See the [sensor session proxy guide](docs/sensor-session-proxy.md).

Start with the [documentation index](docs/README.md), the [implemented settlement interface](docs/settlement-interface.md) and the [no-budget mock sequence diagram](docs/settlement-sequence.md). These describe the existing scaffold; the infographic above restores the budget gate to the target design without claiming that it is implemented. The [research comparison](docs/research/wallet-micropayments-comparison.md) is background research, not a statement of implemented capabilities.

Earlier concepts and visuals are preserved under [docs/archive](docs/archive/README.md). Use the infographic above for the current target concept and the implementation documentation for what the mock actually does.

## What is included

- **Mock wallet service:** FastAPI, persistent SQLite settlements, two synthetic wallet bindings, separate API and approval credentials, immutable amounts, expiring quotes and simulated receipts.
- **Home Assistant component:** UI configuration, five actions, five sensors, durable interval ledger, status polling and change events.
- **Replay tools:** a command-line debit/credit/zero demonstration and a Home Assistant sample script.
- **Tests:** settlement arithmetic, duplicate/concurrent requests, approval roles, quote replacement, persistence and real HA runtime component tests.
- **Deployment options:** local Python service or Docker Compose. No reverse proxy required.

The service is a new application-specific API, not a BRC-100 wallet server. A later adapter can translate this contract into actual wallet operations; the candidate server documents `createAction`, `signAction` and `internalizeAction`, but those methods are not called by this mock ([candidate wallet server](https://github.com/Calhooon/bsv-wallet-cli)).

## Quick start: local Python

Use Python 3.13 or 3.14. Run all commands from the extracted `bsv-ocpp-poc` directory.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt

export MOCK_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export MOCK_APPROVAL_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export MOCK_DB="$PWD/data/wallet.sqlite3"

python -m uvicorn wallet_service.app:create_app --factory \
  --host 127.0.0.1 --port 8091
```

Keep those environment variables available to the CLI. Either use a second terminal with the same values or stop the foreground service and restart it using your normal local process manager. Generate the tokens once for a test installation; changing them requires updating HA and any CLI environment.

Do not reuse a real wallet secret as either token. The HA API token cannot access the mock approval endpoints, and the approval token should not be placed in HA.

With the service running, in a shell containing the same token values:

```sh
. .venv/bin/activate
python scripts/mock_cli.py demo
```

This command deliberately simulates approval for three newly generated test sessions. It should print:

| Case | Net AUD account | Direction | Synthetic sats | Result |
|---|---:|---|---:|---|
| Debit | +0.60 | Driver to operator | 6,000 | `mock_received` |
| Credit | -0.30 | Operator to driver | 3,000 | `mock_received` |
| Zero | 0.00 | None | 0 | `no_payment_due` |

Every run uses new session IDs. Mock receipts start with `MOCK-`; `txid` is always null. The conversion is a synthetic constant of 10,000 satoshis per AUD, not a market rate, and network fees are simulated as zero.

## Docker alternative

Set both token environment variables as above, then:

```sh
docker compose up --build -d
docker compose logs -f
```

The Compose definition binds port 8091 to loopback only and persists SQLite in a named volume. The application runs as a non-root user. The Docker build recipe is supplied but was not built in this sandbox.

For a separate HA device, intentionally change the port binding to an appropriate host/LAN address and firewall access to the test machines. HTTP is permitted by this mock scaffold for an isolated test network; it sends bearer tokens unencrypted. Use direct HTTPS on shared or untrusted networks. This is an exception for mock testing, not a proposed live-payment deployment.

## Home Assistant OS app alternative

The repository also provides a dedicated **BSV Wallet Mock** app/add-on, separate from the HACS integration. Add `https://github.com/purcell-lab/ha-bsv-settlement` to the Home Assistant app store repositories, install the app and configure its two mock tokens.

Follow the [app setup guide](bsv_wallet_mock/DOCS.md). It uses persistent `/data` storage, grants no Supervisor/host/device privileges and has no host port mapping by default. The Dockerfile pins the wallet-service source to a specific commit.

## Install with HACS

This repository supports the **HACS custom repository** installation path. It is not included in the default HACS catalogue, and neither HACS nor Home Assistant has certified the payment functionality.

The declared minimum is Home Assistant **2026.9.4**, the version used for the runtime component tests. HACS installs only `custom_components/bsv_settlement`; it does not deploy the separate mock wallet service, install Docker, configure a real wallet or connect your charger.

1. Start the mock wallet service using the local Python or Docker instructions above.
2. In HACS, open the three-dot menu and choose **Custom repositories**.
3. Add `https://github.com/purcell-lab/ha-bsv-settlement` and select type **Integration**.
4. Find **BSV Settlement (Mock PoC)** in HACS and download it.
5. Restart Home Assistant.
6. Open Settings, Devices & services, Add integration and choose **BSV Settlement (Mock PoC)**.
7. Enter the service URL and API token, then run the sample script below.

These menu steps follow the documented [HACS custom-repository process](https://www.hacs.xyz/docs/faq/custom_repositories/). The repository uses the single-integration folder structure and root metadata described by [HACS integration requirements](https://www.hacs.xyz/docs/publish/integration/) and [general requirements](https://www.hacs.xyz/docs/publish/start/).

When a new release is available, update through HACS and restart HA. The service remains a separate deployment; review release notes for API compatibility before updating either side. Back up both the HA storage and service database.

### Manual installation alternative

1. Back up your Home Assistant configuration.
2. Copy `custom_components/bsv_settlement` into `/config/custom_components/bsv_settlement`.
3. Restart Home Assistant.
4. Open Settings, Devices & services, Add integration, then search for **BSV Settlement (Mock PoC)**.
5. Supply the service URL and `MOCK_API_TOKEN`. Do not supply the approval token.
6. Use the sample script below or call the actions from Developer Tools.

The URL is the service origin, such as `http://192.168.1.20:8091`, with no `/v1` suffix. `127.0.0.1` means the HA process's own network namespace: it will not reach a separate Docker container, HA add-on or another computer. For the bundled add-on, use its actual Supervisor hostname on port 8091.

The component rejects a service whose health response is not `mode: mock`. There is deliberately no live-mode switch.

HA supports custom components under the configuration directory, action descriptions in `services.yaml` and coordinated polling; the scaffold uses these mechanisms ([HA file structure](https://developers.home-assistant.io/docs/creating_integration_file_structure/), [HA service actions](https://developers.home-assistant.io/docs/dev_101_services/)).

### Run the HA credit example

Copy the mapping from `examples/ha_demo_script.yaml` into `scripts.yaml`, preserving your other scripts, then reload scripts. Run **BSV mock credit demonstration** and select the integration config entry.

The script:

1. Binds a synthetic session to `driver-demo-01`.
2. Records 3 kWh import at AUD0.30/kWh and 2 kWh export at AUD0.60/kWh.
3. Reconciles the interval sums against the supplied final session deltas.
4. Prepares an AUD0.30 credit and requests simulated payer approval.

It stops at `awaiting_approval`. Read `settlement_id` from the settlement-status sensor's attributes. On the service machine, explicitly simulate the payer's decision:

```sh
python scripts/mock_cli.py status <settlement_id>
python scripts/mock_cli.py approve <settlement_id>
# Or: python scripts/mock_cli.py decline <settlement_id>
```

The approval is acted on by the mock service, not a real driver or operator wallet. HA refreshes within about 15 seconds, or you can invoke `bsv_settlement.refresh`.

Optionally simulate a later confirmation:

```sh
python scripts/mock_cli.py confirm <settlement_id>
```

This sets `mock_confirmed` and `simulated_confirmations: 1`. Real chain `confirmations` remain zero and `txid` stays null.

## HA interface

All actions require `config_entry_id`. Except for `refresh`, they also require `session_id`.

| Action | Additional input |
|---|---|
| `bsv_settlement.bind_session` | `started_at` with timezone; `driver_binding_id: driver-demo-01` |
| `bsv_settlement.add_interval` | `interval` object below |
| `bsv_settlement.prepare_session` | `ended_at`, `final_import_wh`, `final_export_wh` |
| `bsv_settlement.request_payment` | None; uses the stored quote |
| `bsv_settlement.refresh` | None |

`add_interval` is an explicit addition to the earlier interface draft. It makes the scaffold runnable without guessing OCPP entity names. A future adapter should translate validated meter and tariff data into these actions.

Example interval:

```yaml
start: "2026-10-02T02:00:00Z"
end: "2026-10-02T02:30:00Z"
import_wh: 3000
export_wh: 2000
import_price_aud_per_kwh: "0.30"
export_price_aud_per_kwh: "0.60"
price_status: final
tariff_version: demo-v1
meter_quality: validated
```

This synthetic 30-minute interval assumes constant prices. For a real session, supply intervals at the actual tariff boundaries. Prices are signed decimal strings in AUD/kWh; directional energy quantities are nonnegative integer Wh.

An exact repeated interval is ignored. Conflicting overlaps, gaps at finalisation, missing timezone, provisional prices, invalid numbers and mismatched final energy totals are rejected. The `validated` label is a caller assertion, not independent physical-meter validation.

The formula is:

```text
sum(import_kWh × import_price - export_kWh × export_price)
```

Round the final AUD account once to cents using `ROUND_HALF_UP`. A positive net amount means the driver owes; a negative amount means the operator owes. Negative prices are supported.

### Sensors and events

The device exposes session import energy, session export energy, signed net AUD amount, settlement state and quoted satoshis. It shows the latest selected session; older records remain in storage.

The net AUD sensor is populated after preparation, not as an estimated running cost. The service state and receipt fields distinguish a simulated payment from a blockchain receipt.

The `bsv_settlement_status_changed` event includes session ID, settlement ID, state and `mode: mock`. A restart may re-emit the current status. Events are advisory; storage remains authoritative.

## Wallet-service contract

Authentication is `Authorization: Bearer <token>`. The interactive OpenAPI documentation is available on the local service at `/docs`, with a machine-readable schema at `/openapi.json`; the API itself still requires an Authorization header. The included `openapi.json` is generated from this implementation.

| Endpoint | Credential | Purpose |
|---|---|---|
| `GET /v1/health` | API | Mode, network, backend capabilities |
| `GET /v1/wallet-bindings/{binding_id}` | API | One of the two synthetic identities |
| `PUT /v1/settlements/{uuid}` | API | Create/reuse immutable settlement |
| `POST /v1/settlements/{uuid}/request-payment` | API | Create/reuse simulated approval request |
| `GET /v1/settlements/{uuid}` | API | Read current state and receipt |
| `POST /v1/settlements/{uuid}/quotes` | API | Replace an expired, unsubmitted quote |
| `POST /v1/mock/settlements/{uuid}/decision` | Approval | Simulate approve/decline |
| `POST /v1/mock/settlements/{uuid}/confirm` | Approval | Simulate a later confirmation |

The CLI and tests show complete request examples. `PUT` returns 201 on creation, 200 on an identical repeat, and 409 on conflicting content or a second settlement for the same session. Validation failures return 422; missing/wrong tokens return 401.

The request carries `net_amount_minor` as signed AUD cents, directional Wh totals, a SHA-256 digest of the frozen ledger and a pricing summary. The service checks summary arithmetic but trusts HA for the underlying tariff and meter evidence.

### Approval and quote rules

The fixed synthetic quote expires after five minutes. Approval must identify the current quote and approval request, and stale requests cannot be used after a quote replacement.

In HA, call `prepare_session` again with the original finalisation inputs to replace an expired quote, then call `request_payment`. The frozen energy account and settlement ID remain unchanged.

Repeated approval returns the same receipt. Opposite decisions conflict. A declined account stays outstanding; automatic reopening of declined payments is intentionally not implemented.

### Persistence and recovery

The service uses database transactions and unique session/settlement identifiers. Concurrent approvals serialize and produce one mock receipt. HA saves its settlement UUID and immutable request before calling the service, allowing a timed-out prepare to be retried without creating another record.

Service data lives at `MOCK_DB`; HA data lives in a versioned `.storage` record per config entry. Preserve both if restarting or moving the demonstration. Do not manually edit storage while either service is running.

This mock has no external blockchain side effect. Its successful restart/concurrency tests do not establish exactly-once behaviour for a future live wallet backend.

## Testing

Core tests:

```sh
python -m pytest -q tests/test_poc.py
python -m compileall -q wallet_service custom_components scripts
```

Optional HA runtime tests require a Python version supported by the installed Home Assistant release:

```sh
python -m pip install "homeassistant==2026.9.4" pytest-asyncio
python -m pytest -q tests
```

The HA tests use real Home Assistant classes for action registration, storage, configuration form and sensors, with an in-process HTTP transport to the mock service. They do not connect to your HA instance.

See `TEST_RESULTS.md` for the actual verification performed. Dependency ranges are provided for the mock service; they are not a security-reviewed production lockfile.

## Deliberate omissions before live operation

- **OCPP adapter:** map your actual transaction, connector, meter counters and end-of-session signals. Neither charger control nor CSIP-AUS/HAEO behaviour is changed.
- **Tariff adapter:** ingest reliable, timestamped final import/export prices and establish defensible interval energy allocation.
- **Wallet binding and approval:** prove identity possession and integrate the chosen driver wallet's approval/receipt mechanism. Mock identities are not verified real identities.
- **Live quote and fees:** implement an agreed conversion source, actual fees and wallet minimums. The fixed mock conversion never creates a nonzero sub-satoshi amount.
- **Transaction lifecycle:** implement actual signing, receipt verification, recipient acceptance, broadcast ambiguity, reconciliation and chain confirmations.
- **Operational hardening:** authentication review, transport security, rate limits, retention, backup/restore, clock handling and key custody.
- **Distribution:** HACS custom-repository metadata, release-based integration updates and a separate HA OS mock-service add-on are supplied. There is no default HACS catalogue listing or live wallet adapter.

BRC-29 payment delivery requires transaction and remittance/proof handling, not merely a transaction ID; that is a later wallet-adapter task ([BRC-29](https://bsv.brc.dev/payments/0029)).

The first live milestone should remain a small, supervised operator-to-driver credit followed by the reverse payment. Neither should be enabled by simply renaming `mock_received`.

## Licence

The project's original code and documentation are available under the [MIT licence](LICENSE), copyright 2026 Mark Purcell. Referenced or quoted third-party material, dependencies, names and trademarks retain their respective rights; this repository does not relicense them.

## Driver wallet connection and spending approval

The [BSV Browser driver page](docs/driver-session-budget.md) supports approval
before charging. A private link loads the operator terms and live Amber prices;
one page action connects the wallet, signs a capped spending mandate and returns it
to the operator. New version 2 mandates authorise one final automatic debit to the
named operator address, subject to the total limit, fee cap, fixed conversion rate,
expiry and wallet transaction permission. Old version 1 receipts stay consent-only.
The operator binds advance approval to the driver's session.

This is signed spending authority, not a wallet transaction signature or a funded
reservation. Automatic collection is **not connected yet**. The existing manual
payment-review flow remains separate and does not consume these mandates.
