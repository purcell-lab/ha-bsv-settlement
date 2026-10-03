# Metering quality: warnings and hard stops

## Warning-only flags

These flags remain visible and stored, but do not veto an otherwise authorised
settlement:

- `import:energy_without_matching_state`: charging energy and reported running
  state did not match.
- `export:energy_without_matching_state`: export energy and reported running
  state did not match.
- `interval_energy_allocation_estimated`: energy is allocated across price
  intervals using an estimate.
- `not_a_final_bill`: the sensor-proxy account remains provisional.

The last two flags were already non-blocking. The change makes the two state
mismatch flags non-blocking as well. It does not treat a state mismatch as proof
of accurate metering. Operators accept that provisional interval allocation and
timing may differ from a certified meter or final bill.

## What still blocks

Missing meter baselines, counter resets/decreases, incomplete history, unknown
running-state flags and unrecognised future flags remain hard stops. So do
unpriced energy, estimated tariff intervals, unavailable/non-finite amounts,
invalid energy quantities, open sessions, changed frozen accounts, invalid driver
authority, disabled payment policies, insufficient funds, amount/fee caps and
duplicate or uncertain transaction guards.

The warning allowlist is deliberately explicit. An arbitrary new flag cannot
silently loosen financial controls.

## Audit and interface

The original `quality_flags` remain in frozen accounts and signed collection
quotes. Operator credit summaries and receipt energy metadata expose them too.
The Status card and session tables display a non-blocking metering warning
separately from actual payment status. The driver page shows warnings with
collection/credit information.

A newly reviewed completed account no longer requires a separate checkbox to
accept these warnings. Account review, explicit waiver decisions, replacement
confirmation and fresh driver consent are unchanged. Existing signed historical
closed-account reviews retain their original terms and hashes.

## Deployment effects

This change does not enable a disabled credit policy or create spending consent.
Once deployed, an already-authorised, otherwise eligible account previously
blocked only by one of the two state-mismatch flags can proceed during the normal
settlement checks. That can include real automatic operator payments if their
policies are enabled. Review that effect before activation.

No payment should be recreated when an existing transaction is submitted,
unconfirmed or confirmed. Existing uncertain transactions stay on their guarded
reconciliation path. This policy is independent of public driver registration.
