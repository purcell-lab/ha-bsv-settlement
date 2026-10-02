# Mainnet operator wallet: guarded proof of concept

This development milestone adds a **separate mainnet wallet**, native driver-input dialogs and guarded operator payments. It does not change the offline testnet wallet, implement the charging budget gate or authorise automatic debits from driver wallets.

## Custody and funding

HA generates the operator key locally. It is stored using private, atomic HA storage with owner-only permissions, but is **not encrypted or hardware-protected**. Any process with HA's privileges and anyone able to read its backups may access it. This is an experimental hot wallet, not an audited custody service.

Make and protect a recoverable HA backup before funding. Use only a dedicated demonstration balance you can afford to lose. Do not delete/recreate the integration or restore unmatched config-entry/key files. The existing identity anchor prevents silently rotating a missing or changed key.

The dashboard exposes two different public values:

- **Owner identity:** a public key identifying this local operator wallet. This POC does not expose a complete BRC-100/BRC-29 receiving endpoint.
- **Receiving address:** a mainnet P2PKH address controlled by the operator key. Use this address for a direct BSV funding transaction. An identity key by itself is not a payment instruction.

Never enter a private key, WIF or seed into driver fields. The integration rejects private-key-shaped inputs.

## Driver input dialog

The `Driver public identity` native text entity opens HA's more-info dialog. Paste the driver's compressed or uncompressed public key and save. A second dialog accepts the driver's mainnet P2PKH receiving address. This separate address must be obtained and confirmed with the driver; the integration does not invent an address from a BRC-100 identity key.

Submitted driver identities are marked **submitted_unverified**. Syntax/curve validation proves only that a key is well-formed, not that the driver owns it or approved a session. Changing either driver field invalidates an unsigned payment draft. Changing them while a submitted or uncertain payment is unresolved is refused.

## Network provider

Read-only UTXO and transaction checks and the explicit broadcast use WhatsOnChain's documented BSV mainnet API. Requests are rate-limited, HTTPS-only, fixed-origin and do not follow redirects. No private key leaves HA. Address/transaction queries disclose public wallet activity to that provider ([API and authentication](https://docs.whatsonchain.com/), [confirmed UTXO endpoint](https://docs.taal.com/core-products/whatsonchain/un-spent-transaction-outputs), [transaction/broadcast endpoints](https://docs.whatsonchain.com/transaction)).

Balances are provider-reported confirmed unspent outputs, excluding outputs flagged spent in the mempool. They are not independent SPV verification and exclude unconfirmed incoming funds. Missing or failed checks show an unknown balance, not zero. Pagination or malformed data fails closed rather than producing an incomplete balance.

## Explicit operator-payment workflow

These are manual **operator-to-driver** wallet payments, not yet automatic settlement of the HA energy ledger. The operator must independently reconcile the energy account and any agreed AUD-to-satoshi conversion. No market conversion rate is invented.

All four mainnet actions require an authenticated HA administrator. Calls with no user context, such as ordinary autonomous automation calls, are rejected.

1. **`wallet_refresh_chain`:** read available confirmed funding outputs and reconcile the current submitted transaction. Does not broadcast.
2. **`prepare_operator_payment`:** supply `config_entry_id`, a unique `reference`, `amount_sats`, and an exact `fee_sats`. The current driver address is the recipient. The response shows the draft ID, address, amount, fee, change and ten-minute expiry. No transaction is signed or broadcast at this stage.
3. **`broadcast_operator_payment`:** repeat the exact `draft_id`, `recipient_address`, `amount_sats`, and `fee_sats`, and set `confirm_mainnet_payment: true`. This is the only real-money action. It signs, script-validates, durably stores the exact signed bytes and input reservation, and then submits that transaction.
4. **`cancel_operator_payment`:** can cancel an unsigned prepared or expired draft, never a signed or submitted transaction.

The POC restricts each payment to 100,000 satoshis and its exact fee to 1,000 satoshis. These are safety caps, not recommended payment sizes or current fee estimates. It requires one provider-confirmed P2PKH input and at least 546 satoshis of change; multi-input spending and sweep-all are unsupported. The chosen fee may be rejected by network policy, which must be handled as a reconciliation issue.

Do not call broadcast until the payer, recipient, amount and fee have been reviewed and approved. Enabling the backend is not authorisation to send a particular payment.

## Payment states and recovery

- **`prepared`:** unsigned, awaiting exact approval.
- **`expired` / `cancelled`:** no transaction was submitted by this draft.
- **`broadcast_unknown`:** exact signed bytes and the input reservation are persisted. Submission may or may not have reached the provider. Never issue a replacement payment.
- **`submitted`:** the provider returned the expected transaction ID. This does not establish network acceptance or confirmation.
- **`provider_unconfirmed` / `provider_confirmed`:** the provider reports transaction visibility/confirmations. This is not independent proof verification or a driver receipt.

Duplicate approval calls return the existing record without sending again. A timeout, provider rejection, response mismatch or process interruption leaves the signed input reserved. Refresh chain status first. This deliberately conservative version has no automatic rebroadcast, manual raw export, signed-input release or replacement-transaction mechanism. If a signed transaction cannot be reconciled, operator investigation is required.

One pending payment is allowed at a time. A unique reference cannot be reused with different terms. Source transaction IDs, amounts and owned scripts are validated against raw transaction bytes before signing; inputs are checked again immediately before submission.

## What is not implemented

Development code now also provides a [session-linked account review](session-payment-review.md):
it freezes a closed proxy account and conversion-rate sensor, issues a manually
fulfilled driver payment request, and separately prepares/approves an operator
credit. This is an additional explicit review path, not automatic charging
settlement or integrated driver-wallet authorisation.

The budget gate, proof of driver identity possession, BRC-29/BEEF receipt delivery, driver-authorised debits, automatic session-to-payment mapping, fiat exchange-rate feeds, independent chain-proof verification, reorg-safe finality policy and production wallet recovery/export remain future work. These gaps are not hidden behind a “paid” status.

The offline self-test remains a fictional-source transaction and never uses real funds. It is separate from the mainnet payment path.

## Validation boundary

Automated tests use real SDK signatures with fictional chain responses. They exercise exact approval checks, invalid identity/address rejection, expiry, immutable references, changed drivers, spent-input checks, restart persistence, admin-only services, timeout recovery and duplicate-call suppression. Passing tests are not evidence of a live mainnet transfer.

### Activated HA deployment

On 2 October 2026, the development branch was installed through HACS and activated after an authorised restart. A separate mainnet entry loaded on HA 2026.10.0b0 with guarded broadcasting enabled. The 45-test suite and official HACS validation passed ([CI run](https://github.com/purcell-lab/ha-bsv-settlement/actions/runs/36964759591)).

The installed mainnet wallet passed its offline identity, fictional-transaction signing and script-verification checks. Native text input actions accepted a temporary public identity and mainnet receiving address; the test values were then cleared. The operator public identity and receiving address survived an integration-entry reload. Existing testnet and mock entries remained loaded and refreshed successfully.

The private HA dashboard now shows separate mainnet, offline testnet and mock sections. Mainnet has owner identity and receiving address, two native driver-entry dialogs, guarded broadcast status and a read-only chain refresh button. There is no one-click send-money button.

The provider returned HTTP 404 for the newly generated, unfunded operator address. The integration retained an **unknown** balance and a chain-check error rather than claiming a verified zero balance. Receiving details remain locally valid, but live funding discovery and transaction submission have not been demonstrated.

No mainnet payment was prepared, funded or broadcast during deployment. Public documentation deliberately omits the operator identity/address and private site/config-entry identifiers. Make and verify a protected recovery backup before funding.

The integration code remains MIT. The BSV SDK dependency retains its own [Open BSV licence](https://github.com/bsv-blockchain/py-sdk/blob/master/LICENSE.txt).
