# Weekly-first portal wording

## Scope

This is a presentation correction, not new spending authority. The portal
previously displayed monthly defaults even when the station returned
`monthly_enabled:false`. A failed station request also looked like a deliberately
disabled feature instead of an unavailable backend.

The corrected portal has four explicit states:

| State | Presentation |
|---|---|
| Loading | Neutral “Charging approval” copy; no allowance claim |
| Weekly (`monthly_enabled:false`) | Weekly approval explanation and existing invitation route |
| Monthly (`monthly_enabled:true`) | Existing monthly experiment presentation, unchanged in purpose |
| Unavailable or malformed response | Explicit station-service warning, no asserted approval |

The weekly panel labels 1,000 sat for up to seven days as the **default invitation**.
It does not claim that a particular driver has that balance, limit or expiry.
The exact signed invitation remains authoritative.

The panel explains fees within the limit, per-session settlement, no credit refill,
no automatic renewal and the need for an available wallet/session page. Sign-in
does not approve spending. The wallet drawer uses generic permission labels and
directs the driver to the signed invitation rather than inferring consent.

Dormant monthly allowance, renewal and cancellation controls are hidden when
the experiment is disabled. Historical monthly authority records remain labelled
as historical evidence; they are not relabelled as weekly signatures. Hiding the
experiment does not revoke any native wallet permission previously granted.

## Validation

`frontend/driver/station-flow.test.js` covers default versus personal terms,
loading/unavailable/malformed responses, explicit experimental mode and guarded
portal presentation. Existing financial, invitation, session and wallet tests
remain the owners of authority semantics.

Browser QA uses intercepted fictional responses only:

- Weekly mode on desktop and mobile, light and dark.
- Signed-in drawer with no monthly allowance or false “approved” claim.
- Tab switching and wallet-drawer open/close.
- Unavailable backend with explicit warning.
- Explicit monthly mode retaining the experiment's own wording.
- No native wallet, payment or receipt operations.

No integration settings, invitations, signatures, recipients, fees, limits,
payment policies or ledger records are changed. Deployment and HA restart
require separate approval.
