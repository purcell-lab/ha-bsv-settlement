# Metering-quality warning policy: QA

## Coverage

- State mismatch and provisional-allocation flags retain their original values in
  the frozen account; net amount and source record remain unchanged.
- Automatic single-session and ongoing credits with warnings submit once and
  reconcile the same transaction. Warnings are visible in credit summaries.
- A driver collection with a warning can produce a signed account quote, but
  still needs driver claim, draft validation and permission. A duplicate
  authorisation is rejected; permission alone never broadcasts.
- Missing baseline, reset/decrease, incomplete history, unknown flags, unpriced
  energy, estimated tariffs, unavailable/invalid amounts and invalid energy stop.
- Disabled policy, revoked approval and excessive payment amount stop.
- Completed-account warnings no longer need separate metering acceptance. Account
  review and fresh driver consent remain mandatory.
- Existing reviewed account hash inputs are unchanged.
- Status banner and table warnings are separate from actual payment status.

## Visual and functional checks

Exercise the fictional **Metering warning: settlement allowed** preview scenario
at desktop and mobile widths, in light and dark modes. Verify the warning remains
visible without a false “Data review required” status, then switch back to a
confirmed-payment scenario and verify warnings do not remain from another session.

Check that lack of approval still shows review/consent requirements, not readiness.
Do not connect a real wallet, enable a payment policy or release a held session.

## Local result

On 3 October 2026, the independent warning-policy branch passed 543 Python tests,
40 operator frontend tests and 66 driver frontend tests. This includes 27 new
warning-policy checks; two superseded tests that required a separate warning
acceptance checkbox were replaced by tests of the new warning-only behaviour.

Playwright verified the warning scenario and its return to a confirmed-payment
scenario at 1280px/light and 375px/dark. The warning was visible separately from
credit readiness, no horizontal overflow was observed, and the warning cleared
when the displayed session changed. No live payment was attempted.
