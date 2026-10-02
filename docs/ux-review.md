# Settlement UX review and redesign

## Findings

The prior interface showed integration internals before decisions. It also
contained outdated statements that automatic payments were unavailable, and a
hard-coded "not requested" label that could appear on settled sessions.
Approval, session and payment records were not joined in the operator view.
The driver page retained approval instructions after approval and confirmation.

## Design decisions

- **Operator overview:** show the latest session, matching payment status and one
  next action. Never use an earlier session's approval as evidence of readiness.
- **Driver setup:** a three-step invitation, approval and session-matching flow.
  Default limits are visible; editing contact and limits is optional. Private
  links have copy and QR controls and remain in memory only.
- **Payments:** separate automatic credits from manual exceptions. Use plain
  status labels and keep full references and transaction data in expandable
  details. Preserve typed values during status updates, but reset payment
  consent when the reviewed terms or state changes.
- **Wallet:** show confirmed spendable funds and pending change separately,
  alongside the provider-check time and a read-only refresh action.
- **Driver page:** adapt the headline, progress and action to approval, session,
  submission, confirmation and wallet receipt import. "Receive credit in wallet"
  imports an existing confirmed receipt; it does not issue another payment.
- **Diagnostics:** keep conversion settings, offline tests and mock results away
  from day-to-day settlement controls. Retain stable dashboard URLs.

This applies [visibility of system status](https://www.nngroup.com/articles/visibility-system-status/)
and [progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/):
show the state and next action first, with technical detail available on demand.
The interface uses labelled controls, keyboard focus indicators, touch targets
of at least 44 pixels, responsive layouts and light/dark themes.

## Safety boundaries

Payment authority, signature verification, exact-recipient approval, expiry,
session binding, caps, signed-input exclusions and one-submission behaviour are
unchanged. Read-only summaries contain no private link, token, signature or raw
transaction. A readiness indication is not a payment guarantee. Driver wallet
import is shown as accepted only after the wallet returns `accepted: true`.
The operator does not infer native wallet receipt acceptance from confirmation.

The periodic balance check now also reconciles the existing manual payment,
without creating, signing, replacing or broadcasting a transaction.

## Validation inventory

This is the release checklist. Backend/SDK tests cover cryptographic and payment
error cases; isolated browser checks cover presentation and user interactions.
It is not a claim that every error was reproduced in a real BSV Browser.

- Desktop and 375-pixel mobile layouts in light and dark themes.
- Operator overview: unpaid, missing approval, pending, confirmed, unavailable.
- Wallet: confirmed plus pending change, missing balance, provider failure.
- Invitations: create, copy fallback, QR, limits, refresh, open-session matching.
- Manual review: consent gates, exact recipient/fee, expiry, no duplicate send,
  input preservation during refresh and read-only viewer restrictions.
- Driver: entry without invitation, signed invitation, approval, registration,
  waiting for session match, confirmed credit, receipt accepted, wrong wallet,
  stale prices and network failure.
- Build reproducibility and backend regression suite with fictional wallets.

Browser QA uses isolated fictional data. It is not a live payment trial or proof
of compatibility with every BSV wallet or browser.

## Local validation results

The Python/Home Assistant regression suite passed 160 tests, with 21 driver SDK
and view tests and four operator display tests also passing. Browser checks completed the
fictional approval, receiving-key registration, session match, automatic credit,
confirmation and wallet-import sequence. The fictional broadcaster recorded
exactly one submission.

Operator browser checks cover invitation creation, QR, copy, status refresh,
session matching, input preservation and administrator-only approval gates.
Desktop and mobile screenshots use the actual custom cards with a fictional
Home Assistant state adapter. They are not screenshots of the authenticated
native Home Assistant shell.

Opening another invitation in the same tab now reloads the page. This prevents
the old session's wallet state from being shown under a new private link.
The mobile spending button stays visible with its total cap and fee allowance,
then disappears after approval. Screenshots were checked at 1280 and 375 pixels
in light and dark themes, without horizontal content overflow or page errors
in the exercised flows.

## Rollout

Install the validated integration revision and restart Home Assistant to load
the new static route and read-only summary attributes. Then register
`/bsv_settlement/operator-card.js` as a dashboard module and refresh the existing
budget and review modules with a revision query parameter. Apply
`frontend/dashboard.py: redesign` to a backup of the existing five-view dashboard.
It derives configured entry and entity IDs and retains existing view paths.
The helper recognises the redesigned section views on subsequent runs and
preserves user-added cards, extra views, ordering and dashboard metadata.
It only repairs the budget card's wallet-status reference from the review card.
A partial or ambiguous redesigned layout raises an error instead of rebuilding
it destructively; inspect the backup and make a targeted patch in that case.
Keep the backup for rollback. The driver page retains its existing static URL.

Do not create a payment, change a recipient, change an approval or alter the
automatic-credit policy as part of this UI rollout. After activation, inspect
the native desktop and mobile dashboard and reconnect a driver wallet only
with the driver's participation.

For the fictional operator preview, run `npm ci && npm run build` from
`frontend/`, then serve `preview/`. Its controls cannot call the live system.
