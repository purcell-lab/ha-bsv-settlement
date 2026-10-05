# Validation and test-suite ownership

One full release gate, smaller feedback loops, no live money in automated tests.
Use Python 3.14, Home Assistant 2026.9.4 and Node 22, as pinned in CI. Install
`requirements-dev.txt`, the pinned HA package and `pytest-asyncio`; run `npm ci`
in both `frontend` and `frontend/driver`.

## Commands

From the repository root, using the prepared Python environment:

| Command | Purpose | Release evidence |
|---|---|---|
| `python scripts/validate.py quick` | Distribution, arithmetic and tariff feedback | No |
| `python scripts/validate.py python` | Every Python regression, HA runtime and compile check | Python portion |
| `python scripts/validate.py driver` | Every driver test and reproducible shipped bundle | Driver portion |
| `python scripts/validate.py operator` | Every operator test and reproducible cards | Operator portion |
| `python scripts/validate.py all` | Union of the three release portions | Local full gate |
| `python scripts/validate.py all --list` | Review the exact commands without executing | No |

The GitHub gate also includes HACS validation. Queued, cancelled or runner
infrastructure failures are not passing evidence. Do not bypass this gate.

Build checks compare tracked files to the committed source tree. Rebuild and
review changed artifacts before committing a feature, then rerun validation.

## Rationalisation decisions

- Discover all top-level `*.test.js` files in each JavaScript package. New tests
  can no longer be silently omitted from manually maintained npm script lists.
- Keep backend, driver and operator as separate owners of distinct contracts.
  Contract overlap is intentional where both trust boundaries must enforce it.
- Keep restart/checkpoint, duplicate collection, uncertain broadcast, allowance,
  fee, receipt acceptance and wrong-driver regressions. No safety tests removed.
- Keep the full Python directory in release CI. Focused tests aid development,
  but cannot replace the release run.
- Fail early when the full Python environment lacks HA or pytest-asyncio,
  rather than accepting import-skipped HA coverage.
- Report the slowest 15 Python tests before optimising fixtures or adding
  parallel execution. Shared HA cleanup and imported fixtures need isolation
  evidence first; do not blindly enable xdist.
- Cancel obsolete runs for the same PR, never unrelated PRs. Pin Ubuntu 24.04
  and Node 22 rather than depend on moving runner labels.

## Evidence boundaries

Automated tests use fictional wallets and in-process HA instances. They are
not native BSV Browser acceptance, chain verification, physical ownership
evidence or authorisation to move funds. Native acceptance remains a separately
recorded gate in `monthly-release.md`.
