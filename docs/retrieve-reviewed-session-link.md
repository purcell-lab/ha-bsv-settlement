# Retrieve a reviewed session's original private link

## Purpose

A driver can lose the private page for an earlier session while retaining a page
for a newer session. Do not create another spending approval to collect the old
account. Its existing collection owns that session and must remain authoritative.

The administrator-only `bsv_settlement.get_reviewed_collection_link` action
redisplays the original link for an unclaimed `recovery_ready` collection.
It is separate from ordinary status reads, which continue to hide approved links.

## Safeguards

- Requires an authenticated HA administrator, the exact budget ID, the current
  quote hash and explicit `confirm_private_link_disclosure: true`.
- Only accepts reviewed collections that still require driver confirmation.
  Refuses a claim, permit, signed transaction or submission.
- Checks the existing approval, expiry, frozen account, manual-payment conflicts
  and session ownership.
- Regenerates only an HMAC-derived original capability and verifies its stored
  hash. Legacy random or mismatched capabilities are refused, not replaced.
- Does not save state, rotate a token, extend expiry, change a quote, clear a hold,
  create a new approval or initiate collection.
- The driver HTTP endpoint cannot invoke this action. Wallet identity and fresh
  driver confirmation are still required for collection.

## Administrator procedure

Read `session_budget_status` for the specific reviewed budget. Pass its
`collection.quote.hash` as `expected_quote_hash` to the new action with the same
config entry and budget IDs and the explicit disclosure confirmation.

Append the returned `driver_link_fragment` to the instance's existing HTTPS driver
page URL. Give this complete link only to the approved driver through a private
channel. Do not put it in GitHub, logs, dashboard entity attributes, analytics,
shared screenshots or public documents.

The driver must use the original approving wallet, verify the session reference,
amount and fee cap, and select **Review and resume collection**. Link retrieval
itself neither connects a wallet nor authorises a transaction.

## Deployment

This change adds a backend HA service and requires a separately authorised
restart after installation. Verify the exact service schema and unchanged
approval/collection before retrieving the link. Do not submit a payment as part
of deployment testing.

## Validation

Local validation passed: 344 Python/Home Assistant tests, 53 driver tests and
20 operator tests, for 417 total. This includes 23 new test cases covering
successful repeat retrieval, persistence reload, unchanged saved state, no public
capability leakage, exact quote checks, missing disclosure confirmation,
administrator enforcement, unsupported driver HTTP access, expiry, revocation,
changed accounts, legacy links, hash mismatch, collection ownership, and refusal
after claim, permit or transaction evidence.

Python compilation, whitespace checks and reproducible frontend builds also
passed. No live capability was retrieved or payment initiated during development.
