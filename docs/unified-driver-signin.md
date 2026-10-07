# One driver sign-in journey

This change simplifies the weekly-first UI. It does not enable monthly authority,
change payment limits, recover held transactions or alter operator credit policy.

## Layout

- One primary sign-in action at the top, with the budget and expiry disclosed.
- Wallet status with distinct evidence for identity, connection, budget,
  receiving registration, receipt checking and native wallet payment permission.
- Current buy/sell rates and live session energy, average prices and net account.
- Collapsed history, with one expandable row per session.
- Collapsed wallet options, pairing QR/text/copy and BSV technical details.

## Authority boundary

One application action can request multiple wallet prompts. It does not bypass
them. Identity uses the existing signed challenge. New public registration uses
the existing operator-signed invitation, spending signature and receiving proof.
The displayed invitation is re-read and compared before signing. Approval save
ambiguity latches setup; it is never retried automatically.

A public invitation can be used only while registration is open. Sign-in cannot
create an invitation, claim an unrelated session or reopen closed registration.
After approval the driver goes to the existing private charging page, which
retains per-session collection, fee checks, holds and duplicate-payment guards.
Returning portal users can receive existing credits, but the static portal does
not become a new debit worker. They still need their existing charging page for
collection. This limitation must not be labelled “ready for automatic payment”.

Wallet APIs being present is not proof of a native spending grant. The UI never
checks that permission as granted based only on createAction/signAction methods.
Receipt checking being enabled is not acceptance of any particular transaction.
History keeps provider confirmation separate from wallet-reported acceptance.

## QA inventory

Use offline fixtures only. No production wallets, registration capabilities,
transaction retries, live HA changes or funds are needed for these checks.

| Case | Required result |
| --- | --- |
| Public signed out | One main sign-in button; live rates visible; private history hidden |
| Returning wallet | Identity and connection separate from budget evidence |
| Budget missing/expired/exhausted | No budget check mark |
| Paused receipt import | No receipt-ready check; no automatic repeat after rejection |
| Public invitation changes | Stop before new signature |
| Rates missing/stale | Stop before spending signature |
| Cross-wallet history | Reject; no private records disclosed |
| Sign-in restored without wallet | History can load; live connection remains unverified |
| Mobile/light/dark | No overflow; disclosures and focus operable |
| Private payment hold | Existing guarded recovery action retained |
| Monthly disabled | No monthly offer or new monthly grant |

Native Yours Wallet and BSV Browser acceptance remains a post-deployment user
test. Offline UI tests cannot prove a wallet will suppress its permission prompts.

## Local verification

- Driver Node suite: 204 tests passed.
- Python suite: 1,588 passed on the full run; one distribution-copy check failed
  because the generated stylesheet had not yet been copied. After copying it,
  the 66-test portal, weekly-mandate, budget and distribution run passed,
  including the previously failing check and the added expiry/revocation test.
- Production bundle rebuilt; source/distribution stylesheet copies match.
- Chromium: public sign-in, new-budget signature/receiving/handoff, existing
  weekly reuse, history expansion, sign-out clearing records, restored cookie
  without wallet, wrong network, declined permission and held-debit sign-in.
- Held-debit sign-in: zero claim, draft, sign, report or approval calls.
- Public portal inspected at 375 px and 1,280 px, light/dark; no horizontal
  overflow or JavaScript errors on the final checked states.
- Private page inspected at 375 px and desktop; its offline history challenge
  and budget approval complete without payment drafting.

Build the isolated fictional preview with
`cd frontend/driver && node build-unified-preview.mjs`.
Its output is `preview/unified-driver`. Do not use these fixtures as native-wallet
acceptance evidence or ship them as the installed driver entry point.
