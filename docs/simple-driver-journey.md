# Simple driver journey

## Scope

Retain the existing BSV wallet, signed budget, automatic collection and operator-credit method. This is a presentation change, not MFP, a payment channel, a new spending mandate or charger control. The driver-facing steps are **Authorise budget → Charge / export → Settle**.

For a new invitation, the existing authorisation handler connects the wallet and signs the budget through one primary action. Wallet-specific identity, spending and transaction prompts may still appear. History sign-in remains a separate, read-only permission; it never becomes spending approval.

## Research and design decisions

- **Show the next action, not every function:** defer uncommon controls to one clearly labelled secondary area, following [NN/g progressive-disclosure guidance](https://www.nngroup.com/articles/progressive-disclosure/). The former six-button toolbar becomes one eligible primary action, with pairing, refresh and sign-out under “Wallet options and help”.
- **Explain prices before approval:** disclose energy rates, fees and the total budget before the spending action, consistent with the [Cal-ITP EV payments transparency report](https://resources.calitp.org/calitp/Cal-ITP.EV.Payments.Transparency.Report.December.2024.pdf). Negative buy/sell rates, cumulative multi-session limits, expiry and immediate collection of completed accounts remain explicit.
- **Keep progress and receipts understandable:** the same report recommends live session costs and itemised post-session information ([Cal-ITP](https://resources.calitp.org/calitp/Cal-ITP.EV.Payments.Transparency.Report.December.2024.pdf)). The session page keeps energy and settlement visible; the portal leads with the latest session and retains expandable history.
- **Do not hide safety decisions:** payment holds, broadcast uncertainty, receipt-report failure and unsaved signatures remain actionable. Chain confirmation and receiving-wallet acceptance are not combined into a false success message.

## Interface behaviour

| Situation | Primary experience |
|---|---|
| Public page, new registration available | Authorise EV charging budget opens the existing guarded public invitation. |
| Public page, no registration available | View my charging history; explain that a new budget requires an operator invitation. |
| Valid invitation | Rates, operator, all-in cap, scope and expiry, followed by Authorise EV charging budget. |
| Approved active session | Energy totals and provisional charge/credit; wallet availability guidance. |
| Completed account awaiting permission | Clearly disclose that approval can collect immediately. |
| Submitted/unconfirmed payment | Awaiting block confirmation, with no duplicate-payment button. |
| Unknown/held payment | Clear pause message and operator-help direction; no new payment. |
| Reviewed recovery | Existing guarded review-and-resume flow, retaining the amount confirmation. |
| Signed approval not saved | Save existing approval, not sign again. |
| Signed transaction not reported | Check existing payment, not create another transaction. |
| Confirmed credit awaiting wallet acceptance | Add credit to wallet; explicitly receipt-only. |
| Fully accepted credit or confirmed debit | Clear result; technical references available on demand. |

QR codes retain the full encoded text and a copy button. Registration QR and wallet-pairing QR remain distinct. Technical identity, private links, signed JSON and other historical credits are secondary, not prerequisites for the common task.

## QA inventory

Use only offline fixtures and mocks. Never sign, broadcast or operate a real charger during interface testing.

- **Initial views:** portal and invitation at 375 px and 1280 px; one primary action; no six-button disabled menu; readable fees and rates; no horizontal overflow.
- **Combined approval:** one UI action uses the existing identity and budget signature flow, saves once, and advances to the session; no payment for an open fixture session.
- **History sign-in:** reveals only mock linked sessions without a budget approval or payment; expandable rows and pagination still work.
- **Settlement states:** active, unconfirmed, confirmed, held, unknown and waived; no retry payment offered for held/unknown/submitted states.
- **Credit receipts:** confirmed but unaccepted credit exposes receipt sync; acceptance/reporting remain distinct; no createAction/signAction from portal sync.
- **Off-happy-path:** rejected login, expired login, missing prices, interrupted status and failed receipt report.
- **Secondary controls:** wallet options, session details, approved terms, pairing visibility, selectable QR text, copy fallback, refresh and sign-out.
- **Accessibility:** keyboard disclosure/action use, visible focus, 44 px touch targets, headings, live status, light/dark and 200% zoom.
- **Regression:** driver wallet tests, operator tests, relevant Python HTTP/portal tests, reproducible production bundles and GitHub CI.

## Release boundary

Prepare a reviewable PR and an isolated preview. Do not merge, install, restart Home Assistant, alter approvals or exercise real-value settlement without the user's next deployment instruction. The preview is not evidence of compatibility with a live mobile wallet; that remains a post-install check.

## Verified offline results

Driver tests: 110 passed. Operator tests: 76 passed. The focused HTTP/portal Python tests passed 30 tests. A full local Python run passed 1,027 tests with one generated-file parity failure; rebuilding the distributed assets corrected that failure and its targeted rerun passed. GitHub CI repeats the full Python suite.

Browser checks at 375 px and 1280 px found no page errors or horizontal overflow in the tested states. Light/dark display and 200% zoom were checked. Combined authorisation saved exactly one mock approval and created no payment draft for an open session. Held, uncertain, unconfirmed and expired states offered no new payment/approval action. Declined wallet permission left an explicit error and zero saved approvals.

The history fixture loaded 27 records through pagination, expanded session rows, refreshed and signed out without granting spending permission. Receipt import/reporting was tested both normally and with an interrupted report: recovery kept the import count at one and retried only the acknowledgement. Private-session and public-registration QR text remained selectable, with the clipboard fallback exercised.

Real mobile pairing and real-value settlement were not exercised. The preview clearly identifies fictional data and blocks external wallet/payment requests.
