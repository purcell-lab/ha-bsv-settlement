# Automatic operator-to-driver credits

Development code supports automatic payment when the final session balance is
negative. No per-payment operator approval is required after the operator enables
the policy. The feature is disabled by default in a new installation.

## Payment policy

- **Total operator spend:** at most 1,000 sat per session, including a fixed
  10 sat network fee. The driver receives the full calculated credit, at most
  990 sat. An oversized credit is blocked, not reduced or split into payments.
- **Session account:** closed, fully priced, without blocking quality flags.
  The existing interval calculation combines charging cost and export revenue,
  including negative rates. The invitation's fixed sat/AUD rate applies.
- **Driver destination:** a BRC-29 receiving key derived from the driver and
  operator wallet identities. The driver signs its registration before the
  session ends. The backend independently derives and checks the same key.
  Global manual driver-address fields are not used.
- **Authority:** the operator policy must predate the invitation. Old invitations
  are not upgraded and historical sessions are not paid automatically. Driver
  spending approval alone never grants authority over the operator wallet.
- **Session binding:** pre-session invitations still require the operator to
  identify the physical driver and bind the correct recorder transaction.
  This is not per-payment approval and is not automatic vehicle authentication.

The policy can be enabled once through the authenticated administrator action:

```yaml
action: bsv_settlement.configure_automatic_credit
data:
  config_entry_id: YOUR_MAINNET_OPERATOR_ENTRY
  enabled: true
```

Use `enabled: false`, or **Stop new automatic credits** on the Payments card,
to stop new signatures. This cannot recall a submitted transaction. Re-enabling
requires new invitations; old queued sessions remain for reconciliation.

## Driver and server flow

1. Issue a new invitation after enabling the operator policy.
2. The driver opens the private link in BSV Browser and selects Approve spending.
   The same page action also obtains a receiving key and signs its registration.
   Wallet-native permission prompts can still appear.
3. Bind the correct physical charging session. Record and price each interval.
   In the sensor proxy, a return to `Occupied` after energy flow closes that
   activity session, as do `Ended` and `Idle`. Resumed activity after `Occupied`
   is a new session and needs its own approval.
4. The server checks eligible closed accounts every 15 seconds. A negative account
   creates a frozen credit and reserves that session against both manual payment
   review and automatic driver collection.
5. The operator wallet selects a confirmed funding output, validates the exact
   recipient and amount, then signs. It saves the exact signed transaction and
   input reservation before submitting once.
6. The server reconciles that same transaction ID. Provider acknowledgement is
   labelled submitted, not paid or confirmed.
7. After provider confirmation, the driver page builds Atomic BEEF from the
   transaction and Merkle proof. It checks the receiving output and proof root
   and calls the driver's `internalizeAction` with BRC-29 payment remittance.

The server can pay a registered receiving key while BSV Browser is closed.
The native wallet may not show a spendable balance until the driver reopens the
same private link, reconnects the original wallet and imports the receipt.
Importing the same receipt does not create or broadcast a second payment.
The page allows receipt retrieval after invitation expiry. Expiry or revocation
before the operator signs blocks a new payment.

Driver-to-operator collection remains browser-dependent. This update does not
reserve driver funds or guarantee collection when the browser is closed.

## Failure behaviour

- **No confirmed funding:** keep the frozen credit queued. Retry funding checks,
  but only while the signed terms and operator policy remain valid.
- **Changed account, missing rates or missing destination:** do not send money.
  Display a blocking reason. Never guess a destination or use a replacement
  amount.
- **Unknown submission outcome:** retain signed bytes, transaction ID and input
  reservation across restarts. Query evidence only; never automatically resend,
  replace, cancel or free the input.
- **Other operator payment pending:** wait rather than compete for the same funds.
  Manual and automatic payments share signed-input exclusions.
- **Wallet import failure:** retain the confirmed payment and retry importing
  its receipt after reconnecting. Do not pay again.

## Evidence and limits

### Operator balance refresh

The existing **Confirmed wallet balance** sensor counts confirmed outputs that
are not spent in the provider's mempool or reserved by a locally signed payment.
It does not count unconfirmed change as spendable money. Its `pending_change_sats`
attribute shows expected change from locally signed, unresolved operator payments
separately. An unknown broadcast is included in this estimate, not asserted as
received. No combined "available balance" is calculated.

The integration refreshes this balance on startup, after an operator submission,
and when an automatic credit first confirms. It also checks every five minutes,
or every minute while an operator payment is unresolved, on the existing
15-second coordinator cycle. These checks only read chain data. They do not sign,
resubmit or replace payments. A provider error makes the balance unavailable,
sets `chain_error`, records `chain_attempted_at`, and uses the same bounded retry
interval. Payment confirmation and signed-input reservations remain unchanged.
The administrator's **Refresh chain** action remains available.

For example, spending a 5,000 sat output to pay 280 sat plus a 10 sat fee leaves
4,710 sat in pending change. If another 76 sat output is untouched, the sensor
temporarily reads 76 sat, with 4,710 sat pending. After confirmation and provider
indexing, the confirmed balance becomes 4,786 sat and pending change becomes zero.

The provider supplies transaction confirmations, the TSC proof and its block
header data. A matching Merkle root is checked before wallet import, but this
service does not independently validate the header chain. See the provider's
[transaction proof endpoint](https://docs.whatsonchain.com/transaction) and
[block endpoint](https://docs.whatsonchain.com/block).

The implementation uses the pinned `@bsv/sdk` 2.0.13 interfaces for BRC-29 key
derivation, Atomic BEEF and wallet import, and `bsv-sdk` 2.4.0 for operator signing.
Automated tests use fictional wallets and a recording fake provider. They do not
establish native BSV Browser compatibility, live miner acceptance of the fixed
fee, or successful mainnet settlement. No real test payment is part of installation.

This remains an experimental hot-wallet proof of concept. The conversion rate
is a demonstration rate, meter allocation is provisional, and no charger stop,
escrow, certified billing or unattended production assurance is claimed.
