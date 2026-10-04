# Estimated tariff settlement policy

Estimated import and export tariffs are disclosed warnings, not settlement vetoes,
when a closed session has complete pricing coverage and valid measured energy.
This follows the operator's instruction to allow payment of a calculated account
without another data-review step solely because some rates are estimates.

## Narrow change

- `import:estimated_tariff` and `export:estimated_tariff` join the explicit
  non-blocking warning list in the backend and operator/driver interface.
- Every unit of energy must still be priced: both unpriced-energy counters must
  be present and exactly zero.
- Estimated-energy counters must be present, finite, non-negative and no greater
  than the corresponding recorded energy, allowing only float conversion noise.
- Nonzero estimated energy must retain the corresponding warning flag.
- Missing prices, unresolved conflicts, missing counter baselines, invalid
  amounts, open sessions and unknown quality flags remain blockers.

## Payment boundaries

This does not approve a wallet, change a recipient, increase a limit, release a
hold, or permit duplicate payment. Existing signed terms, funding checks, fees,
source-account checks and payment ownership still apply.

The account and warning flags are frozen when payment is prepared. Later changes
in tariff evidence must not automatically create a replacement payment, refund or
top-up. Any adjustment is a separate reviewed action.

The existing account snapshot schema is unchanged, preserving hashes for already
frozen accounts. No historic signed terms or payment records are migrated.

## Activation

Installation and restart require separate approval. Activating this policy may
allow an already-closed, estimate-only-blocked account to proceed under existing
automatic settlement authority. Verify its original route, fixed conversion and
transaction history before and after activation; do not redirect it to a newer
wallet registration or manually retry it.
