# Validation for rapid prototyping

Routine changes use a focused weekly-settlement gate, not the entire Python
archive. The full regression suite remains intact for releases, manual checks
and broad changes. Automated tests never use live funds.

Use Python 3.14 and Node 22. Install `requirements-dev.txt`, `pytest-asyncio` and
one supported Home Assistant version. CI checks 2026.9.4 and 2026.10.0b3, with
separate clean-install jobs using only HA and the manifest requirements. Run
`npm ci` in `frontend` and `frontend/driver`.

## Everyday commands

| Command | Scope |
|---|---|
| `python scripts/validate.py` | Default prototype gate and Python compilation |
| `python scripts/validate.py prototype --changed-from origin/main` | Core gate plus changed test files and matching backend tests |
| `python scripts/validate.py quick` | Small distribution, weekly-budget and tariff feedback loop |
| `python scripts/validate.py driver` | All driver JavaScript tests and reproducible bundle |
| `python scripts/validate.py operator` | All operator JavaScript tests and reproducible cards |
| `python scripts/validate.py python` | Every Python regression, explicitly requested |
| `python scripts/validate.py all` | Full Python and both JavaScript suites |
| `python scripts/validate.py --list` | Show the selected commands without running them |

The prototype core retains full test modules for weekly budgets, current-session
inclusion, wallet identity, registration, debit collection, automatic credits,
receipt acceptance, dynamic fees, metering, tariffs, persistence and rollback.
Interruption tests check both payment directions. Exhaustive monthly, OCPP
shadow, retention and historical matrices remain in the full suite rather than
running on every unrelated interface edit.

The changed-area selector is deliberately conservative. It adds a changed Python
test or the matching `test_<module>.py` for a changed backend. An unmapped backend,
deleted path, fixture change, shared runtime/storage boundary or standalone
wallet-service change triggers the full Python suite. An invalid Git base fails
the command rather than quietly selecting fewer tests. This is not a complete
dependency graph: reviewers must request full regression for cross-module
behaviour or a change outside the focused gate's assurance.

## GitHub gate

- **Normal PR and main push:** prototype core plus changed-area tests on both
  supported HA versions; all driver/operator tests and reproducible builds;
  clean-install smoke tests and HACS validation.
- **Manual full regression:** run the Validate workflow with
  `full_regression=true`. It runs the complete Python directory on both versions.
- **Version tag (`v*`):** full Python regression automatically. This does not
  create or authorise a tag or a release.
- **Before a versioned release:** explicitly run full regression on the intended
  commit before tagging. A prototype-green PR is not full-release evidence.

No tests have been deleted, skipped through import failures or randomly sampled.
Both prototype and full validation require HA and pytest-asyncio to be installed.
No xdist/shared-state parallelism was introduced. CI cancels only superseded
runs of the same PR or ref.

The smaller gate trades exhaustive regression coverage for shorter feedback
cycles. A prototype deployment still needs an exact commit, green applicable
checks, protected backup, explicit deployment authority and post-restart checks.
See the [release checklist](release-checklist.md).

## Evidence boundaries

Offline tests and clean installs cannot prove native wallet acceptance, chain
confirmation, wallet receipt acceptance, production recovery or permission to
move funds. Keep those checks separate. Never resend a confirmed or uncertain
payment merely to test an interface.
