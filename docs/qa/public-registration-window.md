# Public registration window: QA plan

## Required checks

| Claim or control | Check |
|---|---|
| Open registration for next driver | Cancel makes no call; confirm creates seven-day, 1,000 sat aggregate terms and explicitly opens the reviewed invitation |
| Exact scope and no hidden policy changes | Check only create/open service calls; disabled ongoing-credit policy and historical records unchanged |
| Public window is bounded | 15-minute expiry, no retry extension, expiry survives restart |
| Historical privacy | No previous identities, sessions, receipts or private links in public discovery/read |
| New driver claim | Valid signature claims under the existing lock; only one concurrent winner; QR then unavailable |
| Failed or lost approval | Bad signatures rejected; exact original receipt can recover the private link after a lost response |
| Close registration | Explicit confirmation; closes public capability, not private invitation; reopen rotates public token |
| Registration changes | Stale operator review rejected; later change to another consent/recipient closes public window |
| UI safety | Non-admin and unavailable recorder disable controls; visible error on failed create/open |
| Visual layout | Desktop 1280px and mobile 375px, light/dark; no horizontal overflow; open/closed status and primary controls legible |

## Boundaries

Use fictional keys and mocked chain services only. Do not open a live invitation,
restart Home Assistant, change real payment policy or initiate a payment for QA.
Opening public registration does not resolve historical metering warnings.

## Local results

Validated on 3 October 2026 with fictional data:

- 536 Python tests passed, including 18 new enrolment-window tests.
- 38 operator frontend and 66 driver frontend tests passed.
- Operator and driver bundles rebuilt successfully; no source diff whitespace errors.
- Playwright exercised cancel, open, close and reopen through real controls.
- Cancellation made no mutation call. Opening called only invitation creation
  and the explicitly confirmed registration-window service.
- An already signed invitation could not be silently replaced. Non-admin controls
  were disabled, and a simulated network failure did not advertise an open window.
- Simulated driver approval hid the public registration controls.
- Desktop 1280px/light and mobile 375px/dark were visually inspected. No horizontal
  overflow, clipped controls or unreadable labels were observed in those states.

The preview uses a simulated service layer. Live public QR and real wallet signing
are not exercised by this UI preview; in-process HTTP tests cover the server-side
public handoff, concurrency, private-link separation and receipt retry.
