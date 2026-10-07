# Experimental early incoming-credit delivery

This PR builds on PR #127's event timestamps. It is disabled by default and must
not be described as tested BSV Browser compatibility until the native acceptance
checks below pass. No payment is created by receipt delivery.

## Protocol and supported first version

After an operator credit is signed, acknowledged by the broadcaster and observed
unconfirmed by the chain provider, the authenticated receiving wallet can request
an early receipt. Only new acknowledged submissions after the operator enabled
the experiment qualify; old, uncertain and previously-confirmed/reassessed
payments do not enter the early path.

The package contains the exact existing signed transaction and one confirmed
parent transaction with a checked provider Merkle proof. The initial adapter
supports only a single input backed by a confirmed operator-owned P2PKH output.
Unconfirmed parent chains, missing/invalid ancestry, oversized packages or
unsupported shapes defer to confirmed delivery. There is no recursive lookup,
fabricated proof, second signature or replacement payment.

The client verifies the recipient, amount, fee/total ceiling, funding outpoint,
funding script, spending scripts and the parent's Merkle root before constructing
Atomic BEEF. It does not attach a fabricated Merkle path to the unconfirmed child.
The native wallet remains responsible for header-chain validation and its own
unconfirmed-payment policy; local `scripts only` verification does not claim
independent chain verification.

The wallet is called with BRC-100 `internalizeAction`, `wallet payment` output
metadata and the original BRC-29 derivation data:

- [BRC-29 payment remittance](https://bsv.brc.dev/payments/0029)
- [BRC-95 Atomic BEEF](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0095.md)
- [BSV SDK BEEF ancestry](https://hub.bsvblockchain.org/bsv-code-academy/sdk-components-reference/sdk-components/beef)

## State and reporting

An early receipt has `delivery_stage: unconfirmed_ancestor_proven_v1`.
Its acknowledgement uses a version-2 signed payload binding that stage to the
original payment, receiving approval, driver identity and exact output.
A version-1 acknowledgement cannot mark an unconfirmed payment accepted.
An early acknowledgement also requires the server's persisted offer marker.

Successful native import is displayed as **Received · unconfirmed**.
`wallet_reported_accepted` does not change the payment's `provider_unconfirmed`
state or its confirmation count. PR #127 records the signed acceptance report
time independently of the later provider confirmation observation.

Current transaction status remains authoritative after a reorg or provider
error. An earlier receipt acceptance never overrides `broadcast_unknown`.
No spendability promise or wallet-native label (including `nosend`) is made.

## Fallback and failure handling

- Disabled experiment: existing confirmed receipt delivery is unchanged.
- Missing supported ancestry: defer once per early job in the open page. A
  later confirmed job has a separate queue key and uses the normal receipt.
- Wallet refusal, interruption or ambiguous native import: pause receipt sync
  for review rather than repeatedly prompting. Native import/reimport
  idempotency must be verified in the compatibility test.
- Successful import but failed acknowledgement: retain the import result and
  signed stage in the page's retry cache; reporting retries do not import again,
  including if provider confirmation arrives meanwhile.
- Once a signed acceptance is saved, confirmed polling does not import a second
  credit. The wallet's ability to update that same record after mining is a live
  acceptance gate, not an assumed feature.
- Disabling the experiment stops new early offers. Reporting a receipt already
  offered remains allowed with the correct proof; no payment or consent changes.

## Operator-controlled trial

The administrator-only `bsv_settlement.configure_early_credit_delivery` service
takes `config_entry_id` and `enabled`. It refuses context-free calls. It changes
only receipt-delivery policy, not automatic-payment authority or weekly consent.
The setting is persisted inside the existing automatic-credit policy namespace
and exposed as `automatic_credit.early_receipt_delivery_enabled`.

Enable only after separate user approval to conduct a live trial. Use a new
small credit to the intended authenticated wallet; do not use historical held
payments, old receipts, or change recipients to make a test succeed.

## Native BSV Browser compatibility checklist

Automated tests establish encoding and application safeguards, not native
wallet behaviour. Record wallet/app version and transport before a trial.

1. Confirm the exact original identity and receiving registration.
2. Enable the experimental receipt policy with administrator approval.
3. Obtain separate approval for the test payment amount and provider-quoted fee.
4. Submit one credit only. Record its txid and PR #127 timing fields.
5. Observe `internalizeAction` accepting the Atomic BEEF before the child is mined.
   Record the wallet's actual label and whether funds are merely visible or spendable.
6. Check the signed acknowledgement is recorded while provider confirmations
   remain zero. No outgoing driver transaction or new authority should be created.
7. After mining, verify HA confirmation and the wallet's original record update;
   no duplicate receipt, credit or payment should appear.
8. Test refusal/transport interruption in fixtures first. Only test receipt
   retries live after inspecting the existing wallet action and exact txid.
9. If native acceptance or later record updating is unsupported, disable the
   experiment and retain confirmed-only delivery. Do not resend the payment.
