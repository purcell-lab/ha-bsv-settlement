# Tariff overlap reconciliation

## Problem

The recorder can observe a thirty-minute estimated tariff followed by five-minute
final tariffs covering part of the same period. Previously, any overlap emptied
the entire retained price series. This made both current and previous session
accounts unavailable, even outside the overlap.

The operator card also interpreted an unavailable account as a driver charge,
appended `sat` to `Unavailable`, and incorrectly implied that a healthy conversion
sensor was unavailable.

## Selection policy

- Use effective start/end timestamps, not arrival time, for energy allocation.
- For identical boundaries, prefer final observations, then the latest revision.
  Equal-priority observations with different rates remain ambiguous.
- Split different boundaries into non-overlapping, half-open intervals.
- Prefer final rates over estimates on each interval.
- Conflicting final rates across different boundaries remain ambiguous. Do not
  choose one merely because it arrived later.
- Where only estimates exist, prefer the newest observation; for tied observation
  times prefer the narrower interval. Unresolved tied rates remain ambiguous.
- Preserve gaps. Never fill a gap with zero, the current price or a carried rate.
- Apply ambiguity flags only where recorded energy intersects the ambiguous
  interval. Retain valid pricing elsewhere, including earlier sessions.
- Preserve negative import/export prices. Retained estimated tariffs remain
  explicitly flagged; this change does not relax settlement-quality policy.

Energy allocated to an ambiguous or uncovered interval remains unpriced, and the
session net amount stays unavailable. Sensor-derived accounts remain provisional,
not certified bills.

## Interface

An unknown AUD account displays `Session amount unavailable` and, for tariff
gaps or conflicts, `Tariff reconciliation required.` It does not assume debit or
credit direction, append a currency unit to an unavailable value, or hide a valid
conversion rate. A valid zero balance has a neutral label.

Existing payment records take precedence in the settlement status, even if a
subsequent recorder calculation is unavailable.

## Deployment and recovery boundary

This PR changes tariff reconstruction and presentation only. It does not change
signed mandates, recipients, payment records, fee policy, wallet actions or
duplicate-payment guards, and introduces no retry or broadcast action.

Reconstruction can restore a previously unavailable session account. Under
existing automatic-settlement policy, deploying that correction may make an
unpaid account eligible for processing. Deployment therefore requires separate
approval and a read-only review of restored accounts and existing payment IDs.
This PR does not authorise installation, restart or payment.

Review the current and previous session separately after installation. Preserve
the original IDs and any existing confirmed transaction. Do not create a
replacement payment for an account already paid. If only estimated historical
prices remain, disclose that limitation rather than treating them as final.
