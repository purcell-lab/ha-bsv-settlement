# Settlement trust boundaries and review checklist

Draft assurance work for #2, #5 and #24. This is not an independent security
assessment, legal opinion or permission to expand the demonstration.

## Boundaries

| Boundary / abuse case | Current control | Evidence still required |
|---|---|---|
| Driver impersonation or modified consent | Signed canonical terms, scoped identity proof, expiry/revocation | Supported-wallet matrix and independent protocol review |
| Capability link theft | Random fragment capability, server hash, scoped endpoint, no HA admin token | Leakage/referrer/log review, revocation and retention tests |
| Cross-origin or oversized input | Browser-origin checks, JSON size and request limits | Non-browser abuse, global-limit denial-of-service and malformed-input matrix |
| Driver invoking privileged actions | Administrator context for wallet/policy services | Role matrix and unprivileged integration/service probes |
| New registration redirects another vehicle's credit | Verified registration, recipient frozen per session | Operator accepts physical-driver attribution assumption; race/revocation tests |
| Ongoing authority mistaken for driver consent | Separate operator-credit policy and spending mandate | Clear lifecycle/expiry/re-enable documentation and device UX review |
| HA or backup compromise | Local private atomic storage and identity anchor | Protected backup drill; acknowledge key is not encrypted/hardware protected |
| Duplicate or changed payment | Immutable hashes, exact bytes, shared ownership and input exclusions | Complete interruption/concurrency/stale-restore matrix |
| Provider gives stale or false evidence | Exact tx/output comparisons, provider-labelled states | Reorg reassessment and independent header/finality policy |
| Sensor/tariff tampering or gaps | Unit/quality checks, price windows, frozen accounts | Physical attribution, independent calculations and source trust review |
| Browser is closed or driver refuses | No silent authority expansion; unresolved payment remains | Unpaid-account process; no claim of secured funds |
| Aggregate operator-wallet drain | Per-session 1,000 sat total cap and stop control | Explicit policy for aggregate/daily exposure; currently no cumulative cap |

## Review decisions to record

- Separate account ownership, wallet key control, real-world identity and
  physical-vehicle attribution. None automatically proves the others.
- Distinguish consent, wallet transaction permission, reserved funds and an
  enforced charger budget. Current operation does not provide all four.
- Agree the activity-session boundary and whether repeated Occupied/resume
  cycles may create multiple capped credits. A per-session cap is not a daily cap.
- Define privacy/retention, incident suspension, dispute, price-correction and
  unpaid-account handling. Seek qualified review for consumer, payment, tax
  and privacy obligations rather than assuming an exemption.
- Assign support and incident ownership before a wider pilot. Do not invent
  assignees or imply operator acceptance of unresolved findings.

## Exit record

For each boundary, record test IDs, environment, finding/severity, mitigation,
independent reviewer and residual-risk disposition. Critical findings remain
open and block expansion. Production secrets and private operational details
belong in protected evidence, not this repository.
