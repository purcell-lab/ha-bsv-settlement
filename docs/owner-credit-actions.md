# Owner credit actions

Owner credits means money received by the charging owner from the driver. Driver credits remains a separate direction.

## Interaction

- Unfinished transactions appear before completed history. Each row includes its saved amount when available, actual state, explanation and visible actions.
- Review consent / waiver opens the completed-account control with the exact session already selected. The original account review, reason, confirmation and backend checks remain required.
- View approval / driver link reads that row's budget, never the newest driver's budget. Pending invitations may expose their original QR. Signed approvals without a recoverable link point to the wallet-authenticated driver portal. Expired approvals do not display an actionable private link.
- Review waiver first prepares the existing charge's read-only review. A reason, checkbox and final confirmation are required before the existing waiver service is called. Waiver is not payment, refund or cancellation of an external wallet action.
- Review held collection uses the existing read-only recovery service. Release controls appear only for a pre-signing hold that the server declares eligible. All four evidence attestations, a nonsecret reference and final confirmation remain required. The server compares the original quote and claim before release.
- Check provider status refreshes provider evidence without resubmission. Successful refresh is not a claim that payment was confirmed.
- Manual requests carry a nonsecret review ID in the authenticated payment summary so their existing review can be opened exactly. Missing IDs never fall back to the latest review.
- Driver collection rows carry the exact nonsecret `budget_id` from the saved collection key, including weekly child collections and expired approvals. The ID is independent of quote validity and never comes from an untrusted quote payload.
- Existing collections never borrow another approval for the same session. If an older backend omits the ID, targeted approval, waiver and recovery controls are withheld with “Collection reference unavailable”. Provider refresh remains available for uncertain attempts. Updating the integration restores routing; no ledger migration or new invitation is needed.

Uncertain, signing-authorised and submitted attempts offer reconciliation, not a new consent or payment path. Rendering the page makes no service calls. Links and QR codes are disclosed only after an administrator selects the specific record.

## Display and retention

The work panel opens below the selected transaction. Its fields survive ordinary Home Assistant updates. Missing historical energy and price values remain unavailable, not zero. Completed history, the full data table and manual lookup are secondary expandable sections.

This view covers records available in the existing authenticated sensor summaries. It is not a new paginated archive endpoint. Older records outside those bounded summaries can still be selected through the retained manual session-ID lookup.

## QA inventory

- State model: current and historical sessions; exact budget and manual-review routing; direction filtering; unfinished-first grouping; terminal confirmation requires a transaction ID.
- Backend-to-frontend contract: actual Python payment summaries are passed into the JavaScript action model, covering expired requests and uncertain weekly child collections with competing newer approvals.
- Consent: exact session selection; prepare without mutation; warning disclosure; existing confirmations retained; form values persist after refresh.
- Waiver: read-only preparation; incomplete form blocked; explicit final confirmation; no resend or refund.
- Recovery: uncertain payment has no release or waiver; eligible pre-signing hold requires all attestations; fresh reviewed link uses the original quote.
- Access: viewer and unavailable-wallet controls disabled; no page-load calls.
- Visual: 1280 px desktop, 390 px mobile, light and dark, expanded work panel, no horizontal overflow.
- Privacy: preview uses fictional IDs and data only; live status responses and private links are never stored in preview assets.

No installation, restart, live payment, waiver, hold release or invitation change is performed by this PR.

## Consistent driver pages

Driver portal and Your charging session share a six-action toolbar: Connect wallet, Connect BSV Browser, Authorise EV charging budget, Refresh status, Sync credit receipts and Sign out. The same order and labels are used in both modes. Unavailable actions remain visible, subdued and disabled; a short expandable explanation says why.

The toolbar delegates to each mode's existing controls and permission checks. Portal sign-in cannot approve spending. A private invitation is not a portal login and does not expose an invented sign-out action. Navigating away or closing a page does not revoke a signed approval.

The portal concentrates on wallet-verified history and receipt sync. The private page concentrates on the selected invitation, energy account, consent and settlement. Other credits under the registration exclude the already displayed current session, and duplicate full session references are removed from the pricing card.

Private-session, registration and pairing QRs display the exact encoded value below the QR with a copy button. Expired or cleared pairing codes clear their copyable value too. The existing portal pairing URI remains visible with its copy control. URLs and pairing URIs retain distinct labels; copying or scanning is not spending approval.

## Validation results

- Local Python / Home Assistant suite: 923 passed.
- Operator interface tests: 72 passed.
- Driver wallet tests: 96 passed.
- Browser checks on fictional fixtures: exact historical budget targeting for waiver and recovery; incomplete forms rejected; confirmation dialogs retained; uncertain transaction cannot release; current-session closure ID preselected; form values survive a live update; viewer action buttons disabled.
- Driver browser checks: wallet sign-in, one receipt import and acknowledgement without payment, history refresh with expanded row retained, sign-out hides history, session approval delegates to the original wallet flow, equal toolbar order across both modes, private QR value visible, pairing URI copy fallback and clearing on disconnect.
- Visual checks: desktop light and mobile dark portal, desktop light and mobile light session page, desktop light and mobile dark owner actions; no page overflow or JavaScript errors observed.

These are simulated UI and automated regression results. No real wallet pairing, receipt import, waiver, release or payment was performed during this change.
