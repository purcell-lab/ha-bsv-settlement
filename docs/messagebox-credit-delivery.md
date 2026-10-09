# MessageBox credit delivery (PeerPay-compatible)

Opt-in push delivery of confirmed operator credits to the driver's wallet. Disabled by default.

## Why

Operator credits are already BRC-29 payments to the driver. Until now, a driver's wallet received one only when the driver opened the portal or private link and the page synced the receipt. MessageBox delivery drops the same payment into the driver's `payment_inbox`, so a PeerPay-compatible wallet or app can accept it without visiting the site.

## What is sent

For each eligible credit, the integration sends one MessageBox message to the driver's identity key:

| Field | Value |
|---|---|
| Message box | `payment_inbox` |
| Recipient | The driver identity from the signed receiving registration |
| Body | `{"encryptedMessage": base64}`. BRC-2 encryption to the driver, protocol `[1, "messagebox"]`, key ID `"1"` |
| Decrypted body | `{"customInstructions": {derivationPrefix, derivationSuffix}, "transaction": AtomicBEEF bytes, "amount", "outputIndex": 0}` |
| Message ID | HMAC of the body, protocol `[1, "messagebox"]`, key ID `"1"`, counterparty the driver. The server ignores duplicates, so a retry cannot deliver twice. |

The transaction is the credit already broadcast and provider-confirmed, carried as AtomicBEEF with its Merkle path. The derivation prefix and suffix are the signed `credit_receiving` terms. Output 0 pays the key derived under protocol `[2, "3241645161d8"]` with the operator as counterparty. A PeerPay-compatible wallet accepts it with `internalizeAction` and `protocol: "wallet payment"`, exactly as the driver page does.

## What it never does

- It never builds, signs, funds or broadcasts a transaction, and never changes a credit's amount, recipient or state. It only reads `receipt_for_item`, which already exists.
- It never sends an unconfirmed credit, one the driver's wallet has already reported accepting, or one whose output does not match the registered driver wallet.
- It never pays a delivery fee. Payment-gated hosts fail closed.

## Rollout and controls

- **`bsv_settlement.configure_messagebox_delivery`** (administrator only): `enabled`, plus an optional HTTPS `host` (default `https://message-box-us-1.bsvb.tech`). Enabling records the time and the user.
- **Automatic sending:** only credits created after enablement are sent automatically. At most 2 are sent per run, with up to 3 attempts each, one hour apart.
- **`bsv_settlement.deliver_credit_message`** (administrator only): queues one confirmed, unreported credit by credit ID, including an older one. It never resends a delivered credit.
- **Status:** each credit in the operator card's payment list shows `inbox_delivery` (`state`, `attempts`, `last_attempt_at`, `sent_at`, `error`, `host`). The wallet status shows the policy as `messagebox_delivery`.

## How it runs

Network calls never hold the wallet lock. The 15-second coordinator tick only starts one background run. That run takes the lock to choose credits and freeze each request, then releases it. It sends with the BRC-103 authenticated client from `bsv-sdk` in an executor, with a 75-second limit, and takes the lock again only to record the result.

The auth client uses a minimal operator wallet (`OperatorAuthWallet`) built on the SDK's BRC-42 key deriver. The SDK's `ProtoWallet` is not used: its return shapes do not match what the auth client expects, and it prints diagnostics. The SDK's own auth helpers also print nonce diagnostics, so those are suppressed during a send.

## Privacy and limits

- **What the host sees:** the body is encrypted to the driver, but the host still sees the operator and driver identity keys and the timing of each credit.
- **Host discovery:** the host is configured, not discovered. A driver whose wallet listens on a different advertised host (`ls_messagebox` overlay) will not see the message there. Driver-page sync still works.
- **No acceptance signal:** inbox acceptance is not reported back. The operator card keeps showing "receipt not recorded" until the driver page next syncs and signs its acknowledgement.
- **Early credits:** early (unconfirmed) credits are not sent this way.

## Verification

`tests/test_messagebox.py` covers:
- disabled by default, and administrator-only configuration
- requested and automatic sending
- encrypted body contents
- no payment side effects
- bounded retries
- refusal of unconfirmed, wallet-reported and mismatched credits
- one background run at a time, with the network call made outside the lock

`frontend/driver/cross-messagebox.js` has the official TypeScript SDK do the following on a Python-built message:
- decrypt the message
- parse the AtomicBEEF
- check the derived output and the Merkle root
- recompute the message ID
- verify an auth signature

A live read-only check authenticated against the default host with a throwaway key.
