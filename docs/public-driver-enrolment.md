# Public QR when the charger has no registered driver

The public driver page polls for an unassigned invitation and displays its QR
automatically. The operator must first create a future-session or multi-session
invitation. Public discovery never creates, modifies or approves a budget.
Named current/past-session invitations are not advertised.

## Privacy boundary

The public QR contains a separate `join`/`key` capability, not the private
`budget`/`token` capability. Only unsigned invitation terms and indicative rates
are returned. No driver identities, previous sessions, wallet balances or payment
history are returned.

A valid wallet signature claims the invitation under the coordinator lock.
Public reads stop immediately at acceptance, even before receiving registration.
Only the signing driver's approval response receives the private session link.
The browser replaces its address with that link and continues normal wallet
registration. A copied public QR cannot read the accepted driver's session.
Retry after a lost response requires the exact original signed receipt.

The public QR disappears within the five-second polling interval, or immediately
on a failed availability check. Server-side access is denied immediately; hiding
the QR is not the security control. A registered or approved driver, expired or
revoked invitation, missing latest invitation or ambiguous wallet selection keeps
the public QR hidden. Legacy receiving registrations remain active until revoked;
seven-day receiving registrations stop being active at their signed expiry.

## Scope and limitations

This is walk-up enrolment. Any wallet possessing the public QR may attempt to
claim the unassigned invitation first. It does not prove that person owns or is
physically beside the vehicle. This is not charger access control, charger start
authority, payment or a guarantee of funds.

Use a camera or URL scanner, then open the enrolment link inside BSV Browser.
The BSV Browser “Connect to app” pairing QR is a different protocol and is
available after private-session handoff. No external QR or image service is used.

Only public discovery is polled. It does not poll or display any driver's private
history. All real spending, receiving registration and signed-transaction guards
remain in the existing private workflow.

## Visual and functional QA

Check desktop and mobile layout, top budget-authorisation button, local EV/charger/
wallet graphics, automatic public QR, missing/expired invitation, registered
driver, failed fetch, one-time handoff, stale tariffs, copied public QR rejection,
private link QR and separation from wallet pairing. No real funds are used.
