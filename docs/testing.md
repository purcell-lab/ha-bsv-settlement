# Validation and test-suite ownership

One full release gate, smaller feedback loops, no live money in automated tests.
Use Python 3.14 and Node 22, as pinned in CI. CI runs the Python suite on each
supported Home Assistant version: 2026.9.4 (the declared minimum) and
2026.10.0b2 (the next release). See the
[release checklist](release-checklist.md#supported-versions). Install
`requirements-dev.txt`, one of those HA packages and `pytest-asyncio`. Run
`npm ci` in both `frontend` and `frontend/driver`.

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

`python scripts/clean_install_smoke.py` installs only the HACS payload into an
empty temporary configuration. An isolated interpreter then sets it up through
the config flows, checks entities, actions and frontend paths, and unloads it,
with no network. The Python suite runs it too (`tests/test_clean_install.py`).
CI also runs it in a separate job with only HA and the manifest requirements
installed. `tests/test_upgrade_rollback.py` loads fixture stores written by
earlier releases.

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
recorded gate in `monthly-release.md`. The primary-device checklist and current
interface blocker are in [BSV Browser acceptance](bsv-browser-acceptance.md).
