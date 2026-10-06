# BSV session settlement: HA integration and standalone mock service

Version 0.1.2 | Prepared for Mark Purcell | 2 October 2026

**Session-end settlement proof of concept.** The HA integration offers three backends: the read-only `sensor_proxy` session recorder, the read-only `ocpp_import_shadow` observer and the guarded `embedded_mainnet` operator wallet. The former `mock` (HTTP mock wallet service) and `embedded_testnet` backends have been removed from the integration; existing entries of either fail to load with a clear error and must be deleted (their stored files are left in place; after deleting the entry an administrator can remove them with `bsv_settlement.purge_removed_backend_stores`, giving its `entry_id` and `confirm: true`). The standalone mock wallet service, CLI and add-on below remain for offline experiments only; HA no longer connects to them.

**Mainnet development:** a third, separate operator-wallet backend supports browser-open driver collection and server-side automatic operator credits for negative session balances. Automatic credits require a one-time operator policy, a new signed invitation and a verified driver receiving key, but no per-payment approval. Maximum operator spend is 1,000 sat per session including the [live size-based network fee](docs/operator-fee-policy.md), with no separate fee ceiling. Read the [automatic-credit guide](docs/automatic-operator-credits.md) and [mainnet safety guide](docs/mainnet-operator-wallet.md) before enabling or funding it. This is an experimental hot wallet, not an audited charging-settlement product.

The [embedded operator-wallet record](docs/embedded-operator-wallet.md) is historical (removed testnet backend). No new version or release tag has been created.

## End-to-end concept

![Bidirectional EV wallet settlement concept: BSV budget approval gates session start, OCPP measures imports and exports, Home Assistant applies dynamic prices, and wallets settle the final payment or credit. The budget gate and real wallet connection are planned, not implemented.](docs/images/settlement-infographic.png)

[Open the full-resolution infographic](docs/images/settlement-infographic.png).

**Target design:** retain the BSV budget gate before session start, then meter and price energy dynamically and settle the final payment or credit. Budget authorisation is not a prepayment or a guarantee of available funds. The planned controller must monitor spend and pause or obtain renewed approval before the limit is reached, while preserving charger and grid safety controls.

**Standalone mock:** the mock service exercises synthetic session-end settlement only, through its own CLI. The charger-enforced budget gate and native OCPP meter feed are not implemented. No backend is wired to `bsv-wallet-cli`. The mainnet backend uses the Python SDK; its manual payments require exact approval, while automatic credits use the bounded operator policy.

**Operator wallet:** the mainnet backend uses `bsv-sdk==2.4.0`, not `bsv-wallet-cli`. It provides persistent operator identity, an offline signature self-test and local, never-paying settlement drafts alongside the guarded payment flows.

## Design documents

- [Operator and driver UX review, validation and rollout](docs/ux-review.md)
- [Ongoing operator credits to the last registered driver](docs/ongoing-driver-credits.md)

**Session-linked review:** development code also provides a frozen-account
review card, a manually fulfilled driver payment request and separately
approved operator credits as a manual exception path. The driver wallet remains
external. This manual flow is separate from automatic credits and cannot pay
a session already owned by the automatic flow. Read the
[payment request and credit review guide](docs/session-payment-review.md)
before configuring or using it.

**Live read-only session recorder:** the development branch now includes a
separate `sensor_proxy` backend for cumulative charger import/export counters,
Sigen-style running states and historical interval-price sensors. It provides
stable proxy transaction IDs, automatically updating provisional energy/cost
sensors and recorder-assisted restart recovery. It cannot control a charger
or request a wallet payment. See the [sensor session proxy guide](docs/sensor-session-proxy.md).

Start with the [documentation index](docs/README.md), the [implemented settlement interface](docs/settlement-interface.md) and the [no-budget mock sequence diagram](docs/settlement-sequence.md). These describe the existing scaffold; the infographic above restores the budget gate to the target design without claiming that it is implemented. The [research comparison](docs/research/wallet-micropayments-comparison.md) is background research, not a statement of implemented capabilities.

Earlier concepts and visuals are preserved under [docs/archive](docs/archive/README.md). Use the infographic above for the current target concept and the implementation documentation for what is actually implemented.

## What is included

- **Standalone mock wallet service (not used by HA):** FastAPI, persistent SQLite settlements, two synthetic wallet bindings, separate API and approval credentials, immutable amounts, expiring quotes and simulated receipts.
- **Home Assistant component:** UI configuration for the sensor proxy, OCPP import shadow and guarded mainnet operator wallet, with their actions, sensors and versioned storage.
- **Replay tools:** a command-line debit/credit/zero demonstration against the standalone mock service.
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

Do not reuse a real wallet secret as either token. The API token cannot access the mock approval endpoints.

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

To reach it from another machine, intentionally change the port binding to an appropriate host/LAN address and firewall access to the test machines. HTTP is permitted by this mock scaffold for an isolated test network; it sends bearer tokens unencrypted. Use direct HTTPS on shared or untrusted networks. This is an exception for mock testing, not a proposed live-payment deployment.

## Home Assistant OS app alternative

The repository also provides a dedicated **BSV Wallet Mock** app/add-on, separate from the HACS integration. The integration can no longer connect to it. Add `https://github.com/purcell-lab/ha-bsv-settlement` to the Home Assistant app store repositories, install the app and configure its two mock tokens.

Follow the [app setup guide](bsv_wallet_mock/DOCS.md). It uses persistent `/data` storage, grants no Supervisor/host/device privileges and has no host port mapping by default. The Dockerfile pins the wallet-service source to a specific commit.

## Install with HACS

This repository supports the **HACS custom repository** installation path. It is not included in the default HACS catalogue, and neither HACS nor Home Assistant has certified the payment functionality.

The declared minimum is Home Assistant **2026.9.4**. CI also tests the next release (currently 2026.10.0b2); see [supported versions](docs/release-checklist.md#supported-versions). HACS installs only `custom_components/bsv_settlement`; it does not install Docker, configure a real wallet or connect your charger.

1. Take a Home Assistant backup.
2. In HACS, open the three-dot menu and choose **Custom repositories**.
3. Add `https://github.com/purcell-lab/ha-bsv-settlement` and select type **Integration**.
4. Find **BSV Settlement** in HACS and download it. For anything beyond a test install, choose the exact release or commit you have reviewed.
5. Restart Home Assistant.
6. Open Settings, Devices & services, Add integration and choose **BSV Settlement**.
7. Choose a backend. The default and safe choices are read-only:
   - **sensor_proxy**: records charger counters and prices from existing sensors.
   - **ocpp_import_shadow**: observes OCPP import registers without affecting billing.

   `embedded_mainnet` controls real funds. It requires three explicit acknowledgements; without all three no entry is created. The former `mock` and `embedded_testnet` choices are no longer offered.

The [clean-install smoke test](docs/testing.md) automates steps 4–7 offline on each supported HA version. Upgrades, rollback, owner roles and downgrade hazards are covered in the [release checklist](docs/release-checklist.md).

These menu steps follow the documented [HACS custom-repository process](https://www.hacs.xyz/docs/faq/custom_repositories/). The repository uses the single-integration folder structure and root metadata described by [HACS integration requirements](https://www.hacs.xyz/docs/publish/integration/) and [general requirements](https://www.hacs.xyz/docs/publish/start/).

When a new release is available, follow the [release checklist](docs/release-checklist.md): back up, record the installed commit and the unresolved payments, update through HACS to the exact version, restart HA, then verify.

### Manual installation alternative

1. Back up your Home Assistant configuration.
2. Copy `custom_components/bsv_settlement` into `/config/custom_components/bsv_settlement`.
3. Restart Home Assistant.
4. Open Settings, Devices & services, Add integration, then search for **BSV Settlement**.
5. Choose a backend as in step 7 above.

HA supports custom components under the configuration directory, action descriptions in `services.yaml` and coordinated polling; the scaffold uses these mechanisms ([HA file structure](https://developers.home-assistant.io/docs/creating_integration_file_structure/), [HA service actions](https://developers.home-assistant.io/docs/dev_101_services/)).

## HA interface

These draft-only session actions require the `config_entry_id` of the mainnet operator-wallet entry. Except for `refresh`, they also require `session_id`. They record a frozen, never-paying settlement draft (`no_payment_due` or `blocked_live_settlement`); payments use the separate guarded workflows. `bind_session`, `add_interval` and `prepare_session` (like `wallet_status` and `wallet_self_test`) require an authenticated HA administrator; calls with no user context are refused.

| Action | Additional input |
|---|---|
| `bsv_settlement.bind_session` | `started_at` with timezone; `driver_binding_id: driver-external` |
| `bsv_settlement.add_interval` | `interval` object below |
| `bsv_settlement.prepare_session` | `ended_at`, `final_import_wh`, `final_export_wh` |
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

The net AUD sensor is populated after preparation, not as an estimated running cost. Draft records never carry a receipt or transaction ID.

The `bsv_settlement_status_changed` event includes session ID, settlement ID, state and `mode: embedded_mainnet`. A restart may re-emit the current status. Events are advisory; storage remains authoritative.

## Standalone mock wallet-service contract

HA no longer connects to this service; it is kept for offline experiments with `scripts/mock_cli.py`.


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

Repeated approval returns the same receipt. Opposite decisions conflict. A declined account stays outstanding; automatic reopening of declined payments is intentionally not implemented.

### Persistence and recovery

The service uses database transactions and unique session/settlement identifiers. Concurrent approvals serialize and produce one mock receipt.

Service data lives at `MOCK_DB`. Do not manually edit it while the service is running.

This mock has no external blockchain side effect. Its successful restart/concurrency tests do not establish exactly-once behaviour for a future live wallet backend.

## Testing

Core tests:

```sh
python -m pytest -q tests/test_poc.py
python -m compileall -q wallet_service custom_components scripts
```

Optional HA runtime tests require a Python version supported by the installed Home Assistant release (CI uses Python 3.14 with HA 2026.9.4 and 2026.10.0b2):

```sh
python -m pip install "homeassistant==2026.9.4" pytest-asyncio
python -m pytest -q tests
python scripts/clean_install_smoke.py   # shipped folder only, empty config, no network
```

The HA tests use real Home Assistant classes for action registration, storage, configuration form and sensors, with fictional keys and stub chain providers. They do not connect to your HA instance or the network.

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

This is signed spending authority, not a funded reservation. **Browser-open automatic
collection** now consumes the version 2 mandate after the bound session ends.
The wallet prepares an unsigned draft, the server validates the exact recipient,
account and fee, and the wallet signs with `noSend: true`. After a final
expiry/revocation check, the server persists the signed transaction and submits
it once. Ambiguous outcomes are reconciled, never automatically rebroadcast.

The driver must keep the page open in BSV Browser and allow its native wallet
prompts. After a reload, Resume reconnects only if no attempt was already reserved.
Manual reviews and automatic collection cannot own the same session. Net credits
still use the separately approved operator-credit flow. See the
[collection operating limits](docs/driver-session-budget.md#automatic-collection)
before a supervised real-wallet trial.

## Completed-session resolution

The Payments review screen supports fresh post-session driver consent, documented acceptance of supported provisional metering warnings, an audited waiver of a driver charge, and zero-balance closure. Existing payment attempts and credits owed to a driver cannot be waived through this flow. See the [completed-session resolution guide](docs/completed-session-resolution.md) for the controls, safeguards and validation scope.
