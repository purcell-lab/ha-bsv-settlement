# ADR: Estimated-tariff finalisation

**Status: Proposed. Needs owner approval under #2.** Draft text only. This PR
does not change settlement behaviour. Until the owner records a decision, the
current behaviour (option A) stays in force, as merged in #68.

## Context

Two written rules disagree:

| Source | Wording |
|---|---|
| #11 acceptance criterion | "Provisional/missing prices block final settlement without obscuring estimates." |
| #12 acceptance criterion | "Finalisation requires reconciled energy and acceptable final tariff data." |
| [#68](https://github.com/purcell-lab/ha-bsv-settlement/pull/68) and [estimated-tariff-settlement-policy](../estimated-tariff-settlement-policy.md) | A closed, **fully priced** session may settle when some intervals are priced from estimates. `import:estimated_tariff` / `export:estimated_tariff` are disclosed warnings, not vetoes |
| [metering-quality-policy](../metering-quality-policy.md), "What still blocks" | Listed "estimated tariff intervals" as a hard stop, stale relative to #68 and the code. Corrected to describe current behaviour (#101); the decision below is still open |

The code follows #68. `session_review.WARNING_FLAGS` includes both
estimated-tariff flags. `account_snapshot` still requires zero unpriced energy
and valid, disclosed estimate coverage. Missing or conflicting prices, open
sessions and unknown flags remain blockers.

The operator asked for #68 so that a calculated account could be paid without a
further data-review step when only estimates were available. #11 and #12 were
written earlier and assume a provisional-versus-final gate.

## What this PR adds to make the policy explicit and testable

- Every captured account records per-interval `price_basis` (`final` or
  `estimate`), the source's own estimate flag and the estimated Wh per
  direction ([tariff provenance](../tariff-provenance.md)). The policy decision
  can be audited after the fact.
- `tests/test_tariff_provenance.py::test_adr_current_policy_estimates_settle_disclosed_but_gaps_still_block`
  pins option A. A fully priced account with estimates freezes with the warning
  disclosed. The same session with an unpriced gap is refused.
- Golden fixture g04 shows an estimate-priced account settling, with the
  reference result.

## Options

**A. Keep #68 (status quo).** Estimates may finalise a fully priced account,
always disclosed and frozen with provenance. A later final price never reprices
a paid account automatically. Any difference is a separate reviewed adjustment.
- Amend #11 to read: "Missing or conflicting prices block settlement. Estimated
  prices settle only with disclosure and frozen provenance."
- Amend #12 to read: "Finalisation requires reconciled energy and complete
  tariff coverage. Estimate-priced accounts are disclosed demonstration
  valuations."
- `metering-quality-policy.md` already describes this behaviour (#101).

**B. Restore an estimate gate.** Remove the estimated-tariff flags from
`WARNING_FLAGS`, so accounts wait until final prices arrive or need an explicit
recorded waiver. This needs a separate PR with activation review, because
deployment would re-block closed accounts that are waiting for estimates.

**C. Time-bounded estimates.** Settle on estimates only after a grace period
with no final price (for example 24 hours), or below a value threshold. This
needs a new parameter in #2 and separate implementation and tests.

## Consequences common to every option

- Corrections after payment need a reviewed adjustment workflow linked to the
  provenance version that differs. This does not exist yet (#11). The existing
  manual energy adjustments (#73) are fixed-quantity operator actions, not
  tariff-revision corrections.
- Accounts remain `not_a_final_bill`. Meter reconciliation (#9) is a separate
  gate for "reconciled energy".

## Decision

_Not yet decided. Record here: chosen option, approver, date and the #2
comment link. Then update #11/#12 wording and the policy documents in the same
change._
