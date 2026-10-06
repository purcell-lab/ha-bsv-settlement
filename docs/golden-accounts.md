# Golden accounts and independent verification

Ten fictional, realistic sessions with expected accounts that anyone can
recompute without this integration. The test suite checks that production
session accounts match them to the cent and to the satoshi.

Addresses part of #12 ("Independent golden calculations agree for debit, credit
and zero accounts" and "Negative import/export prices and tariff-boundary
intervals are covered") and #11 ("Negative prices and timezone transitions have
fixtures").

## Files

| Path | Purpose |
|---|---|
| `tests/golden/fixtures/*.json` | Inputs as HA-normalised observation rows, with an `expected` block |
| `tests/golden/reference_calculator.py` | Independent reference calculator: standard library only |
| `tests/test_golden_accounts.py` | Production vs reference, plus a review-level sats/direction check |
| `tests/test_tariff_provenance.py` | Provenance capture over the same fixtures (DST, overlaps, negatives) |

| Fixture | Covers | Net AUD | Direction | Sats |
|---|---|---:|---|---:|
| g01 Brisbane import | debit; meter sample straddles a tariff boundary | 2.51 | driver to operator | 6275 |
| g02 Brisbane V2G | export credit into a price spike | -3.11 | operator to driver | 7775 |
| g03 Brisbane mixed | charge at negative import, discharge at negative/zero feed-in | -0.20 | operator to driver | 500 |
| g04 Brisbane overlap | 30-min estimate overlapped by 5-min finals; estimate revision | 1.53 | driver to operator | 3882 |
| g05 Sydney DST start | 01:30+10:00 to 03:00+11:00 is a 30-minute interval | 0.75 | driver to operator | 1875 |
| g06 Sydney DST end | repeated 02:00-03:00 hour, mixed direction, zero/negative prices | 0.01 | driver to operator | 25 |
| g07 zero balance | equal import and export value | 0.00 | none | 0 |
| g08 half-cent debit | exactly $0.125 rounds to $0.13; sats tie 331.5 rounds to 332 | 0.13 | driver to operator | 332 |
| g09 half-cent credit | exactly -$0.125 rounds to -$0.13 | -0.13 | operator to driver | 332 |
| g10 tiny across midnight | 40 Wh over local midnight and a tariff boundary | 0.01 | driver to operator | 25 |

All entity IDs, counters and prices are fictional.

## Calculation rules (the specification both sides implement)

1. **Session:** opens at the first preparing or active state and closes at the
   next `Ended`/`Idle`.
2. **Energy:** cumulative MWh counters × 1,000,000 = Wh. The baseline is the
   last reading at or before the session opens. A counter increase is
   attributed to the period from the later of the previous reading and the
   session open, up to the reading. Readings after the session closes are
   ignored.
3. **Allocation:** within that period, energy is spread evenly in time over the
   seconds when the running state matches the direction (`Charging` for import,
   `Discharging` for export). If no second matches, it is spread over the whole
   period and flagged.
4. **Tariff selection:** as in [tariff overlap
   reconciliation](tariff-overlap-reconciliation.md). Effective periods apply,
   not arrival time. Final beats estimate. For identical bounds the latest
   observation wins. For estimates only, the newest observation wins, then the
   narrower period. Conflicting rates leave energy unpriced. Gaps are never
   filled. A row without `estimate: false` counts as an estimate.
5. **Money:** import cost = Σ kWh × import $/kWh, and export credit = Σ kWh ×
   export $/kWh, with signed prices. Net = import cost − export credit. Positive
   means the driver pays. Negative means the operator credits the driver.
6. **Rounding:** only at the end. Net AUD is rounded to 0.01 with ROUND_HALF_UP
   (ties away from zero, so -0.125 becomes -0.13). Satoshis =
   ROUND_HALF_UP(|rounded AUD| × sat/AUD) to a whole sat. Direction follows the
   sign of the rounded AUD. Zero means no payment.

## Why the reference is independent

- It imports nothing from `custom_components` (enforced by an AST test) and
  does not use `decimal`.
- It uses a different method. Timestamps become integer UTC epoch seconds, each
  second is evaluated separately, and arithmetic is exact rational
  (`fractions.Fraction`). Production uses an interval sweep with 28-digit
  `Decimal` and aware-datetime arithmetic.
- DST is irrelevant after parsing. Only explicit offsets are used.

Current result: all 10 production accounts equal the reference. The largest
unrounded difference is 3.3e-28 AUD (g04, Decimal precision). Rounded AUD,
direction and sats are identical. **No production discrepancy was found.**

## Verify it yourself

Python 3.9+ only, no dependencies:

```sh
cd tests/golden
python3 reference_calculator.py fixtures/*.json
```

Each fixture prints `matches_expected: true/false` with the recomputed account.
The exit status is 1 on any mismatch. To check production as well:

```sh
python -m pytest -q tests/test_golden_accounts.py tests/test_tariff_provenance.py
```

A third party can also check a fixture by hand. For g08: 500 Wh × $0.25/kWh =
$0.125, which rounds to $0.13, and 0.13 × 2550 sat/AUD = 331.5, which rounds to
332 sat.

## What is still NOT independent

- **Same author and same written policy.** One developer wrote the reference
  and production code from the same documents. A wrong policy would agree
  with itself. An external reviewer or utility bill comparison is still
  required for #12.
- **Meter source.** Inputs are HA counter observations. Meter accuracy,
  sampling, attribution and tariff-boundary energy are validated under #9, not
  here. The time-proportional allocation is itself an estimate
  (`interval_energy_allocation_estimated`).
- **Tariff source.** Prices are as observed in HA. Agreement with the
  provider's published or billed price is not checked.
- **Not covered by the reference:** counter decreases and resets, quarantine,
  partial history, `Occupied` session splitting, HA normalisation, fees,
  network charges, taxes and FX sourcing. These keep their existing
  production tests.
- **Not a bill.** Accounts remain `not_a_final_bill` demonstration valuations.
