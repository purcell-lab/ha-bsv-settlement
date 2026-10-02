# Automatic driver approval QR

The Drivers card displays the QR directly while a valid invitation is waiting
for driver approval. The Overview card displays a compact QR automatically when
an existing pending invitation belongs to the current open session. If no
invitation exists, the operator must create one; the UI never creates consent,
binds a driver, authorises spending or sends money merely to show a QR.

Expired, revoked, approved, paid, unrelated-session and closed-session approvals
do not produce an approval QR. Non-administrators and unavailable source states
are withheld. The URL stays on the current operator origin, with the capability
in the fragment rather than a URL query. QR generation is local, using the
bundled library, without an external QR service.

## Reload support and capability boundary

New invitations use a 256-bit HMAC-SHA256 capability, domain-separated for
driver-link version 1 and bound to the operator key, config entry and budget ID.
Only its hash and scheme marker are saved. The existing authenticated
administrator `session_budget_status` service can rederive and return that same
link while approval is pending, after checking the saved hash. It does not rotate
the capability or change the signed terms.

The capability is not placed in entity attributes, ordinary public budget
records, summaries or the driver read response. Existing service-level admin
checks remain mandatory. Do not log service-response payloads, copy QR captures
to public issues, or treat an authenticated admin screen as public signage.

Old random-token invitations remain valid but are not recoverable. Their QR
can display if the original creation response is still in the card's memory.
After reload, use the original saved link; explicitly revoke and replace a lost
pending invitation if appropriate. This update never silently invalidates an
old driver's link. An already approved invitation is not reissued.

Copy/open controls remain available on the Drivers card. The driver should save
their link for session updates and receipt import. The operator approval QR is
hidden once approval arrives; it is not a receiving-address payment QR.

## QA scope

Automated tests cover stable admin redisplay after reload, hash-only persistence,
public redaction, terminal/legacy handling, strict same-origin URL construction
and current-session matching. The isolated preview uses a fictional invalid
capability only. Real-device scanning and native BSV Browser compatibility remain
separate validation evidence, not established by rendering a QR.

The fictional Chromium preview was checked at 1280 px and 375 px, in light and
dark themes. Copy controls, tab remount, approval and revocation used the visible
controls. Expiry, non-admin and unrelated/closed-session states were also checked
with staged fixtures. Both theme-specific QR crops decoded to the exact fictional
fragment URL. No page script errors or page-level horizontal overflow were found.
These checks do not establish a native-wallet connection or approval.
