# Fee-aware driver invitations

New driver invitations default to a maximum network fee of 1,000 sat. The total spending limit remains 1,000 sat, including the actual network fee, and the default approval duration remains 12 hours.

## Limits and signed authority

- **Actual fee:** The wallet constructs its draft. The integration checks the actual fee before it issues a signing permit. The configured ceiling is not a fixed charge.
- **Effective fee ceiling:** The quote allows the smaller of the signed maximum fee and the remaining total budget after the session payment. For a fictional 89 sat account, a new 1,000 sat total approval with a 1,000 sat fee ceiling allows at most 911 sat in fees, not a 1,089 sat debit.
- **Insufficient headroom:** A charge that consumes the full total limit is blocked when the approval permits a positive network fee.
- **Existing approvals:** Signed terms, existing quotes, attempts and holds are not rewritten. An existing 10 sat fee approval still rejects a 38 sat fee.
- **Operator credits:** This change does not change the separate fee or policy for operator-funded payments to drivers.
- **Mainnet caution:** A 1,000 sat fee ceiling may be large relative to a small energy payment. Operators can lower the fee ceiling before creating a new invitation.

## Create or replace a private driver link

Open Driver setup, then Create a driver invitation. Select the next session or the current open session. The Create private driver link button is inside Change limits or operator contact.

Review the total, fee ceiling, duration and operator contact before creating the link. The pending invitation displays a private link and approval QR.

- **Unchanged pending invitation:** The same settings return its original link and expiry.
- **Changed pending invitation:** Select the explicit replacement checkbox and confirm the new settings. The backend checks the exact pending invitation ID and payload hash, then revokes it and creates a fresh invitation.
- **Signed approval:** Creation does not silently reuse or overwrite it. The UI and backend explain that its limits cannot be edited here.
- **Failed replacement:** Invalid or stale requests do not replace an invitation. A failed persistence operation rolls back the in-memory replacement.
- **While editing:** Background status refresh does not collapse the creation form.

The driver must approve a replacement invitation. Creating a link never authorises payment or starts the charger. Recovery of an already-signed private link remains a separate guarded workflow.

## Draft diagnostics

New failures distinguish an unsigned-draft requirement, malformed transaction data, unsupported transaction shape, missing funding evidence, incorrect payment outputs, invalid fees, fee-limit breaches and total-limit breaches. Where available, the page shows payment, actual wallet fee, fee ceiling and total debit.

The backend accepts only bounded numeric details and enumerated reasons. Browser reports are explicitly unverified, do not release a hold and do not authorise a retry. Previous generic errors cannot retrospectively reveal a fee breakdown.

## Validation inventory

All previews use fictional identities, sessions and amounts. No real wallet transaction is part of these tests.

| Claim or control | Check and evidence |
| --- | --- |
| Default fee is 1,000 sat, total unchanged | Service, backend and frontend unit tests; visible operator inputs |
| Effective fee respects the total | Quote tests for fee headroom; existing-cap regression tests |
| Private link is created from the limits panel | Browser form submission; visible link and QR |
| Same pending settings return original link | Backend tests and browser comparison |
| Changed settings require explicit replacement | Negative browser test; confirmed replacement produces a new link |
| Replacement cancellation has no effect | Dismiss confirmation; verify no create call |
| Signed approval cannot be replaced | Backend tests and disabled UI |
| Invalid or stale replacement is harmless | Limits, hash, ID, persistence-failure and cancellation tests |
| Editing survives background refresh | Browser polling check with changed input retained |
| Fee failure explains why collection paused | Driver fixture showing numeric fee details with zero wallet calls |
| Form and error states fit mobile and desktop | Screenshots at 375 px and 1,280 px, light and dark |

Live deployment requires review and approval. A held collection under an earlier signed fee limit remains held; a new default alone cannot change its authority or make it collectible.

## Local validation result

The local suite passed 363 Python tests, 56 driver JavaScript tests and 24 operator JavaScript tests. Python compilation and whitespace checks also passed.

Browser checks covered creation, unchanged-link reuse, rejected settings changes, replacement cancellation, confirmed replacement, signed-approval protection, blank and over-total fee inputs, next/current scope switching and form persistence through a 15-second poll. The driver fee-error fixture made zero claim, draft, signature or failure-report calls.

Visual checks covered light and dark themes, 375 px and 1,280 px operator views, the pending QR, and the driver fee-error state. No horizontal overflow, overlapping controls or unreadable fee details were found. These are simulated UI results, not proof of mobile-wallet compatibility or live settlement.
