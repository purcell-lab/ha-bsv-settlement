# Linked credit recovery

## Purpose

Link an already submitted low-fee operator credit to one unsent, completed-session
credit. The new payment spends the operator's change from the first payment.
It pays only the second session's credit, not the sum of both credits.
Both transactions and both session records remain distinct.

BSV documents child-pays-for-parent (CPFP) as a way for a child transaction's fees
to cover itself and its low-fee parent. Inclusion still depends on transaction
propagation and miner policy; a quote or broadcast response is not a confirmation
([BSV node documentation](https://docs.bsvblockchain.org/network-topology/nodes/sv-node/frequently-asked-questions/transactions)).

## Narrow exception

Ordinary automatic and manual funding still requires confirmed outputs.
Only an explicitly reviewed historical credit may spend unconfirmed change, and
only when all of these conditions pass:

- The parent is a locally saved, signed, pending operator credit.
- The original parent and child recipient addresses are identical.
- The child comes from an existing completed-session credit route with a verified
  original receiving registration, immutable energy account and conversion.
- The parent's exact bytes and transaction ID match the provider.
- The parent has one input, two P2PKH outputs and an independently rechecked
  script signature. Its input's source is provider-confirmed and mature.
- The parent payment, fee and operator-owned change match the local record.
- The provider reports that exact change output as unspent in the mempool.
- No local signed transaction already spends that change.
- No other pending operator payment or prepared manual payment exists.
- Both credits and both network fees together fit the 1,000 sat combined cap.

Unconfirmed evidence uses the documented
[WhatsOnChain unconfirmed UTXO endpoint](https://docs.taal.com/core-products/whatsonchain/un-spent-transaction-outputs).
Missing, malformed, paginated, duplicate or conflicting evidence blocks recovery.
This is provider-reported evidence, not independent proof of finality.

## Fee calculation

Use a fresh fee quote and the existing validated 227-byte upper bound for the
child. The parent's actual signed byte length is known.

```text
required package fee = ceil(rate_sat_per_KB * (parent_bytes + 227) / 1000)
child fee = max(standalone child fee, required package fee - existing parent fee)
combined spend = parent credit + existing parent fee + child credit + child fee
```

Illustrative calculation: a 225-byte parent paying 38 sat with a 10 sat fee,
plus a 119 sat child at 100 sat/KB, needs a 36 sat child fee.
The new spend is 155 sat; combined credits are 157 sat, combined fees are
46 sat, and combined spend is 203 sat. This example is not a signed payment or
a guarantee of the next live quote. Never send a new 157 sat credit in this case:
the original parent already pays 38 sat.

## Review and execution

1. Call `prepare_operator_credit_recovery` with the child's existing `credit_id`
   and the parent's saved automatic-credit `parent_credit_id`.
2. Read the returned parent transaction, original recipient, child credit, exact
   fee, package totals, review hash and expiry. Preparation does not sign or
   broadcast; funds are checked but not reserved.
3. Obtain explicit approval of those exact terms, including the additional fee
   and use of unconfirmed operator change.
4. Call the existing administrator-only `broadcast_operator_credit_recovery`
   with the exact review hash, child recipient, child amount and child fee.
5. Reconcile both transaction IDs. Supply the child receipt through its original
   session's BRC-29 receiving registration after confirmation.

The usual pending-payment gate is bypassed only for the reviewed parent, only
inside that one authenticated execution call. There is no persistent bypass.
Restart, background processing and re-enabling a policy cannot release the review.
The parent's bytes, outputs, fee and payment record are not replaced or rebroadcast.

Parent evidence, funding and quoted package fees are rechecked before signing.
A parent that confirms, a spent change output, a changed account or a higher fee
requires renewed review rather than silent substitution. The existing explicit
expired-review renewal mechanism remains available. If the parent confirms,
prepare ordinary confirmed-funding recovery rather than a CPFP recovery.
The same signed child is never recreated or automatically rebroadcast after a
timeout, restart or repeated approval.

## Deployment and validation

This is an integration revision, not a requirement for a tagged release.
It depends on the fee-aware operator-payment update. Install the combined
validated commit through HACS, restart once, and verify services and code before
preparing a live review. Installation is not authority to spend.

Fictional-chain tests cover unsigned preparation, exact child funding, both-fee
accounting, original parent preservation, script validation, provider mismatches,
confirmed/disappeared parents, unavailable change, other pending work, changed
consent/account, increased fees, combined cap boundaries, restart holds, receipt
attribution and at-most-once submission. No test contacts a live wallet or miner.
