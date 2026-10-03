# Public driver registration

The public driver page polls for an unassigned invitation and displays its QR
automatically. The operator must first create a future-session or multi-session
invitation. Public discovery never creates, modifies or approves a budget.
Named current/past-session invitations are not advertised.

## Open registration for the next driver

The **Drivers** tab has an **Open registration for next driver** button.
An administrator confirms the public exposure before any write. The button
creates (or retrieves an identical, still-unapproved) seven-day multi-session
invitation with a 1,000 sat aggregate spending limit and a 1,000 sat fee ceiling
within that total. It then opens a **15-minute public QR window** for that exact
invitation. Repeated requests do not extend an open window.

This explicit window is separate from historical settlement authority. It does
not revoke old consent, move an existing session to a new driver, release held
payments, waive charges, repair metering or change either automatic-credit policy.
Previously signed invitations cannot be silently replaced. If a different pending
multi-session invitation exists, review it using the existing replacement controls.

Refresh the public driver page to see the QR. It closes at approval, invitation
revocation/expiry, the 15-minute deadline, or any change to other driver
consents/receiving registrations on that recorder. A lost response can still be
recovered using the exact original wallet-signed receipt. The admin-only
**Close public registration** button invalidates the public QR without revoking
its private invitation. Reopening generates a new public capability; a copied
old QR cannot be reused. Restart preserves the window and its original deadline.

An existing legacy receiving registration must not force an operator to revoke
historical payment authority merely to enrol a new driver. Without this explicit
operator window, the original no-driver privacy gate still applies.

The window opens registration, not charging. Anyone who obtains the public page
can attempt to claim it first. Open it only when the intended driver is ready.
The seven-day spending permission begins through fresh driver approval and
receiving registration; it does not backdate authority to old sessions. Receipt
of a new registration affects future multi-session eligibility under the existing
rules, not the recipient of an already recorded session.

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
the QR is not the security control. Unless an explicit operator window is open,
a registered or approved driver, expired or
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

For the operator window, test open/cancel, close/reopen, fee-inclusive terms,
non-admin denial, stale review hashes, consent/registration changes, restart
persistence, save failure, concurrent claim, expiry and public capability
rotation. Historical records and disabled ongoing-credit policy must stay intact.
