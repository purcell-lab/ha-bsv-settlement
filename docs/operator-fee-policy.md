# Fee-aware operator payments

## Calculation and limits

For each future unsigned automatic operator credit, obtain a live
[WhatsOnChain fee recommendation](https://api.whatsonchain.com/v1/bsv/main/feerecommendation).
The API documents rates in sat/KB through its
[chain-information reference](https://docs.whatsonchain.com/chain-info).

The implementation chooses the greater of `fee` and `mempool_min_fee`, multiplies
by the supported transaction-size allowance, divides by 1,000 and rounds up to
a whole satoshi. The minimum resulting fee is one satoshi; there is no fixed
10 sat fallback.

The current wallet produces exactly one compressed-key P2PKH input and two P2PKH
outputs. Its conservative signed-size bound is 227 bytes: 119 unsigned bytes
plus up to 108 unlocking-script bytes. The actual signed byte length is checked
before any network submission. A different transaction shape must have its own
validated size model, not reuse this bound.

At a quoted 100 sat/KB the allowance gives a 23 sat fee. A 119 sat credit would
therefore cost the operator 142 sat in this example. These are calculation
examples, not a promise of a future quote or mining confirmation.

The automatic and historical-recovery credit plus its fee must remain within
the existing 1,000 sat per-session operator cap. The fee is additional to the
driver's credit. Credits are never reduced to make room for a fee.
There is no separate operator-credit fee ceiling: a 119 sat credit can use up
to 881 sat of that combined limit for its quoted fee. This is headroom, not a fee
target. The actual calculation uses the quoted rate, not the remaining allowance.

## Fail-closed signing

- Both quoted rates must be valid finite JSON numbers in sat/KB.
- Missing, malformed, zero-only or unavailable quotes block new signing.
- Quotes older than 60 seconds, or with future observation times, are rejected.
- Read the quote again after funding/account checks and before signing.
- A rate increase beyond the unsigned automatic fee stops that attempt. The next
  ordinary evaluation can reprice only unsigned work within the same total cap.
- Funding must cover the credit, quoted fee and conservative minimum change.
- A transaction exceeding the supported signed-size allowance is not broadcast.

The observed quote, selected rate, size allowance and fee are retained with the
payment for audit. The operator dashboard displays the actual fee and available
quote metadata; it no longer describes an always-10-sat policy.

## Exact manual approvals and historical transactions

Manual fee choices and prepared recovery fees are not silently increased.
Preparation and signing check them against a fresh quote. If the current required
fee exceeds the reviewed fee, the operator must prepare and approve new unsigned
terms. Expired recovery renewal retains the earlier review history.

Already signed, submitted, uncertain and confirmed transactions retain their
original bytes, transaction ID, outputs and fee. Reconciliation does not depend
on obtaining a new fee quote. This update cannot repair or cancel an existing
low-fee transaction, release its funding, waive its credit, or implement CPFP.

Driver-wallet collections still use the driver's wallet and signed spending
limits. This change does not grant additional driver spending authority.

## Validation inventory

Tests cover the live API shape, rounding, fractional rates, higher mempool
minimum, malformed/outage/stale quotes, exact total-cap boundary, actual signed
size, unsigned repricing, frozen recovery fees, explicit manual fees and
unchanged signed-transaction reconciliation. Driver receipt tests enforce the
actual credit plus fee cap, including credits with fees below ten satoshi.

UI checks: operator-credit policy wording, per-payment fee details, desktop and
mobile layout. Existing pending transaction displays must retain their actual
historical fees, rather than being relabelled with the new quote.

## Recipient-based settlement tabs

Both credit tabs use the same full-width session table and payment-detail layout.
**Driver credits** means owner-to-driver payments; **Owner credits** means
driver-to-owner payments. Each tab also shows the direction in words.
The existing `operator-credits` and `payments` paths remain unchanged,
respectively, so saved links still reach the same payment direction.
The migration preserves payment filters, approvals, controls and user-added cards.
It can be applied repeatedly without changing the result.
