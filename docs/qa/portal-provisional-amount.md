# Driver portal provisional session amount

## Defect

The operator card prices the recorder's live net account. The driver portal instead looked for `amount_sats` on a payment record, although an open session normally has no payment amount yet. This produced “Credit amount unknown” despite a valid provisional account.

## Correction

Expose the owner-scoped session's meter timestamp and frozen conversion rate in the existing history response. Use the same decimal half-up conversion helper as the operator card. Show a clearly labelled provisional credit, charge or zero balance in the latest-session card, compact history row and expanded details.

This does not create, alter or approve a payment. Closed, submitted, uncertain, waived and confirmed payment records retain their existing statuses and amounts. Missing pricing, invalid conversion and stale readings remain unavailable, not zero.

## QA inventory

- Open export fixture: AUD -0.06 at 100 sat/AUD displays provisional credit 6 sat in both card and row.
- Positive and zero balances use charge and balance labels; half-up conversion matches the operator helper.
- Missing/invalid rate, amount, stale/future timestamp withholds the numeric estimate.
- Frozen route rate takes precedence over a later conversion-sensor change.
- Owner isolation and read-only behaviour remain intact.
- Existing confirmed receipt, uncertain payment and closed account displays are not replaced.
- Mobile/desktop layout, light/dark appearance and expanded-row readability.
- Polling updates the estimate; stale cached estimates expire even after a failed refresh.

## Results

- Full Python/Home Assistant suite: 1,044 passed, with three existing dependency warnings.
- Driver suite: 137 passed.
- Read-only backend tests cover both invitation and ongoing-route rates, sensor-rate changes, missing frozen rates, owner isolation and preservation of closed credit amounts.
- Offline Playwright: card and compact row both showed 6 sat from the AUD -0.06 fixture; expanded details showed the same estimate and “Payment: Not created. Session is still in progress.”
- Browser clock advancement beyond the meter freshness limit replaced the estimate with “Provisional amount unavailable” and requested a fresh meter update.
- A confirmed-credit fixture retained its existing 119 sat payment amount.
- Mobile 390-pixel and desktop 1,365-pixel screenshots inspected in light/dark themes. No horizontal overflow.
- No live payment, wallet action, session mutation or Home Assistant deployment was performed.
