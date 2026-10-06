# Embedded operator wallet in Home Assistant

> **Historical record.** The `embedded_testnet` backend (and the `mock` backend) have been removed from the integration. Existing testnet entries now fail to load with a "backend was removed" error and must be deleted; their stored files are left in place. The shared key-custody and offline self-test code now serves only the [mainnet operator wallet](mainnet-operator-wallet.md), where `wallet_self_test` still runs offline.

Development milestone, 2 October 2026. No release/version bump.

## Decision and verified scope

Use the Python `bsv-sdk==2.4.0` directly inside the custom component. The embedded backend needs no CLI, daemon, mock service, reverse proxy or wallet-service URL. The SDK supplies the cryptographic and transaction primitives ([upstream Python SDK](https://github.com/bsv-blockchain/py-sdk)).

The first stage intentionally has no external network operations. It generates a real, dedicated testnet operator identity, persists that identity across reloads and runs actual SDK signing code against a random challenge and a fictional-source transaction. That transaction is never exported or broadcast and is not a payment.

The existing mock entry, mock app and dashboard are retained. The new embedded entry is independent and uses separate storage and sensors.

## Why not the toolbox yet

An isolated Python 3.14 environment installed SDK 2.4.0, toolbox 2.0.2 and HA 2026.9.4 successfully. Inspection and execution showed that the toolbox's documented `Wallet(chain="main")` quick start omits a required key deriver; listing outputs also requires a configured storage provider. Its package metadata identifies it as Alpha ([toolbox code](https://github.com/bsv-blockchain/py-wallet-toolbox/blob/master/src/bsv_wallet_toolbox/wallet.py), [metadata](https://github.com/bsv-blockchain/py-wallet-toolbox/blob/master/pyproject.toml)).

The toolbox was evaluated, not shipped as a dependency. The current embedded backend does not claim to implement a complete BRC-100 wallet, UTXO manager or BRC-29 payment-delivery system.

## Installation

This code is on `main`, not in the existing v0.1.2 release. Install the development branch through HACS only after reviewing the change and deciding when to restart HA. Do not create a release tag solely to deploy this milestone.

After the updated integration code is active:

1. Add another **BSV Settlement (Mock PoC)** integration entry.
2. Select `embedded_testnet`.
3. Read and acknowledge the local-key custody and unfunded-wallet warning.
4. The entry is named **BSV Operator Wallet (Testnet, Broadcast Disabled)**.
5. Call `bsv_settlement.wallet_status` and `bsv_settlement.wallet_self_test` with that entry's `config_entry_id`.

The status should be `ready_broadcast_disabled`. A successful self-test returns `identity_signature_verified: true`, `synthetic_transaction_signed: true`, `broadcast: false`, `network_checked: false`, and `txid: null`. The returned digest is a local test artifact, never a settlement receipt.

## Custody and recovery

- **Operator key:** generated on HA, never requested from the user or supplied through config-flow fields. It is held in a private, atomic HA Store file under `.storage/bsv_settlement.operator_key.<entry_id>`, with owner-only file permissions.
- **Security boundary:** this file is not encrypted and is not hardware-backed. HA administrators, compromised integrations running with HA's privileges, and anyone able to read HA backups may access the key. File permissions are not isolation from HA itself.
- **Driver key:** never generated, imported or stored. `driver-external` is an unverified draft reference, not a verified wallet identity or permission to debit a driver.
- **Recovery:** the public identity is anchored in the config entry. If an established key file disappears or mismatches the anchor, setup fails rather than silently generating a replacement. Restore a matching HA backup; do not delete and recreate a funded wallet.
- **Backups:** protect HA backups as key material. Do not upload `.storage` files to issues, diagnostics or GitHub. Removing an integration does not intentionally destroy its retained key file.

Do not fund this validation wallet. No spend, refund or recovery workflow is available in this milestone.

## Settlement and budget behaviour

The existing `bind_session`, `add_interval` and `prepare_session` actions also work with the embedded entry. Set `driver_binding_id: driver-external`. Meter evidence is supplied explicitly; no charger entities are subscribed to automatically.

Local decimal accounting and immutable settlement records persist in HA. Positive amounts are driver debits, negative amounts are operator-funded credits. A nonzero account has state `blocked_live_settlement`; zero has `no_payment_due`. Satoshi amounts, quotes, transaction IDs and payment receipts remain absent.

`request_payment` fails closed. There is no runtime switch to enable broadcasting, no broadcaster object, no raw signed-transaction export and no imported spendable UTXOs.

The budget gate remains part of the target design, but it is not implemented and this code does not issue a charging-start command. Binding or preparing a draft is not budget approval. Future implementation must establish the driver mandate, cap/expiry, tariff and fee policy, and a safe allowance for meter/control latency before permitting a real session.

## Validation

Local tests use real HA 2026.9.4 classes and the actual BSV SDK on Python 3.14. They cover existing mock behaviour, embedded configuration, testnet/custody guards, key persistence and 0600 permissions, refusal to rotate missing keys, offline identity signatures, fictional transaction signing, debit/credit/zero drafts, immutable records, and blocked payments.

The offline signing test explicitly forbids socket connections. A passing result demonstrates local SDK operation, not network acceptance, wallet interoperability, receipt delivery or mined settlement.

### Verified HA deployment

On 2 October 2026, the development branch was installed through HACS and activated after an authorised Home Assistant restart. A separate embedded testnet entry loaded successfully on HA 2026.10.0b0. All 35 local tests also passed against that version, as well as the minimum supported HA 2026.9.4; GitHub tests and HACS validation passed ([CI run](https://github.com/purcell-lab/ha-bsv-settlement/actions/runs/36963326459)).

On the installed HA instance:

- SDK identity signature verification passed.
- Signing and script verification of a fictional-source transaction passed.
- Wallet status reported `ready_broadcast_disabled`, testnet, no verified balance and no transaction ID.
- Reloading only the embedded entry preserved the operator public identity and saved self-test result.
- The original mock entry remained loaded and refreshed successfully; its latest synthetic credit and existing dashboard configuration remained available.

No network transaction was submitted, no funds were supplied, and no charger-control settings were changed. The budget gate and live settlement remain unimplemented. Operator public keys, private keys and site-specific config-entry IDs are deliberately excluded from this public record.

Before enabling any real payment:

1. Prove dependency compatibility on the installed HA version.
2. Validate a funded testnet UTXO lifecycle and crash-safe spend reservation.
3. Implement and test external driver identity/approval and payment receipt delivery.
4. Implement the budget gate, agreed exchange-rate quote, fee limits and operator approval.
5. Verify signed transactions and chain evidence with separate submitted, accepted and confirmed states.
6. Obtain explicit approval for the network, payer, recipient, amount and maximum fee.

Mainnet, real payments and production charging controls are out of scope for this milestone.

## Dependency licensing

The integration's own code remains MIT. The upstream BSV SDK has its own Open BSV licence; the repository's MIT licence does not relicense that dependency ([SDK licence](https://github.com/bsv-blockchain/py-sdk/blob/master/LICENSE.txt)).
