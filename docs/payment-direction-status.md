# Session payment direction and collection status

The Payments card now has a Session settlement section, separate from the
ongoing-credit policy. It joins records by session ID and displays the driver
collection record in preference to the same session's `no_operator_credit` route.
An existing signed transaction cannot be hidden by a newer unsigned row.

For a valid, unexpired ready collection with a 19 sat quote, the card says:

> Driver payment due: 19 sat
>
> Awaiting wallet collection.

The amount is projected from the retained frozen quote, not recalculated from
today's exchange rate or the running energy estimate. The backend checks the
budget and account session identifiers and exposes only amount, maximum fee,
frozen conversion and expiry. It never exposes the signed payload or driver
identity through this summary.

Wallet collection in progress, authorised submission, submitted, provider
confirmed, expired, uncertain and error states have distinct instructions.
Submitted or uncertain payments say not to pay again. A confirmed state without
a transaction reference is not presented as verified. Missing amounts stay
unavailable rather than becoming zero. Open sessions show accumulating charges
or credits, not a final amount. Zero-account closure is separate from
`no_operator_credit`, which by itself does not establish driver payment.

This change adds no payment, consent, signing, broadcast or charger-control
behaviour. Existing manual payment controls retain their previous safeguards.
The preview uses fictional data. Native wallet interaction and live deployment
are separate acceptance steps.
