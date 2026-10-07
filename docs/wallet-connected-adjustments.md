# Wallet-connected 5 kWh adjustments

Both operator buttons create a separate monetary adjustment at the current,
valid tariff and the configured sat/AUD conversion rate. They do not alter
metered energy, invent a charging session, or consume a weekly charging budget.
The price sign determines the actual direction: a negative export price is a
driver debit, and a negative import price is an operator credit.

## Operator credit

The operator click authorises the existing capped, fee-quoted payment. Its
original receiving registration is frozen. The signed-in portal discovers the
credit in history and imports the existing receipt through `internalizeAction`
when confirmation and proof requirements are satisfied. Receipt acknowledgement
is separate from provider confirmation. An offline wallet can reconnect later;
the operator must not resend the credit to make a receipt appear.

## Driver debit

Only newly created one-click debit adjustments use
`wallet_connected_energy_adjustment_v1`. The original registered identity must
be authenticated in the open portal; discovery alone grants no spending power.

1. Discover the owner-scoped adjustment in the existing serial debit queue.
2. Verify the operator-signed quote against the known operator identity, exact
   review ID, destination, 5 kWh basis, tariff, conversion, amount and expiry.
3. Request the driver's wallet signature over the quote hash and unique claim.
   The wallet description explicitly says this is a separate adjustment, with
   its payment amount and maximum total. It is not a weekly-budget grant.
4. Persist the one-use reservation before `createAction`.
5. Ask for an unsigned, `noSend` wallet draft. Validate recipient, amount,
   confirmed funding evidence, change, actual fee and the 1,000 sat total ceiling.
6. Persist one signing permit, then use `signAction(noSend=true)`.
7. Check the exact signed shape again and persist signed bytes plus
   `broadcast_unknown` before the single provider submission.
8. Reconcile those same bytes through the bounded confirmation scheduler.

The existing 1,000 sat adjustment ceiling includes the network fee. It is not
a fixed fee: the wallet constructs its fee and the actual debit must fit that
ceiling. Wallet-native prompts may appear for the claim, draft or signature.
The portal must remain available for the signing steps.

## Safety and compatibility

- Receiving registration and login are not debit permission. The exact signed
  wallet claim plus wallet transaction approval are required.
- Identity, expiry, revocation, frozen terms and the current registration are
  checked again before the signing permit and before initial broadcast.
- Another tab, a duplicate click, reload or uncertain response cannot create a
  replacement attempt. One unsettled adjustment per direction/recipient stays
  in force, including while awaiting block confirmation.
- A signed payment can be reconciled after expiry or registration changes;
  reconciliation cannot sign or resend it.
- Existing manual requests are not migrated or automatically prompted. Their
  original manual route may already have been used outside the application.
  Such an outstanding request can block a new equivalent button action and
  requires separate reconciliation or authorised closure.
- Historical recipients, payment holds, weekly limits and monthly settings are
  unchanged. No real payment is part of automated tests.
- History shows adjustment basis and applied price, not unavailable metering
  fields or a claim that 5 kWh was physically transferred.

## Acceptance gates

Automated coverage includes authenticated owner isolation, absent consent,
negative tariffs, exact quote arithmetic, changed recipient/terms, excessive
fees, altered signed bytes, two-click idempotency, restart persistence,
confirmation-only recovery, and unchanged weekly authority.

Before live deployment, review the PR and approve installation separately.
Native-wallet acceptance still needs a controlled new adjustment for each
direction, with separate observations for submission, provider confirmation
and credit receipt acknowledgement. Existing uncertain payments must not be
used as replacement-payment tests.
