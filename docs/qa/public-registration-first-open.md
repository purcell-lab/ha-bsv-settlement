# First-use public registration recovery

Fresh `create_session_budget` responses do not carry admin-only registration
context. The operator card now reads the exact returned budget ID before it
prepares the public-window request. A failed read never creates another invitation.
Server-side invitation and context hash checks remain authoritative.

## Verification inventory

- Fresh creation without context: exact status read then one open request.
- Changed invitation, approval, scope, settlement or missing context: fail closed.
- Read failure: no open, retry, replacement, payment or policy change.
- Preview models the actual fresh-create response.
- Browser controls: cancel, first open, close and reopen. Inspect desktop and mobile.
- Deployment: frontend-only diff, exact served bundle and cache-busted resource.
- Do not restart Home Assistant or replace the live invitation for this UI fix.

## Results

All 55 operator tests passed, including 15 new response-contract tests.
Browser testing verified cancel, fresh create/status/open, close and reopen.
Only the expected invitation/window services were called. Desktop and 390 px
mobile inspection passed, with no page-level horizontal overflow.
No Python or driver-page source changed.
