"""Deterministic local validation entry point. No live HA/wallet calls.

Use the same dependency versions as CI. Prototype is the default development
gate. Full release validation remains explicit. No regression tests are deleted.
Commands are fixed argument arrays, never shell-interpolated input.
"""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
QUICK = (
    "tests/test_distribution.py",
    "tests/test_weekly_mandate.py",
    "tests/test_tariff_overlap.py",
)
PROTOTYPE = (
    "tests/test_distribution.py",
    "tests/test_validation_plan.py",
    "tests/test_budget.py",
    "tests/test_weekly_mandate.py",
    "tests/test_current_weekly.py",
    "tests/test_portal.py",
    "tests/test_portal_registration.py",
    "tests/test_portal_debits.py",
    "tests/test_collection.py",
    "tests/test_collection_unconfirmed.py",
    "tests/test_collection_interruption_matrix.py",
    "tests/test_auto_credit.py",
    "tests/test_ongoing_credit.py",
    "tests/test_credit_interruption_matrix.py",
    "tests/test_receipt_ack.py",
    "tests/test_operator_fees.py",
    "tests/test_ledger_checkpoint.py",
    "tests/test_upgrade_rollback.py",
    "tests/test_proxy.py",
    "tests/test_tariff_overlap.py",
)
# Broad trust/storage changes are not safe candidates for a narrower gate.
FULL_GATE = {
    "custom_components/bsv_settlement/__init__.py",
    "custom_components/bsv_settlement/coordinator.py",
    "custom_components/bsv_settlement/mainnet.py",
    "custom_components/bsv_settlement/audit.py",
    "custom_components/bsv_settlement/const.py",
    "custom_components/bsv_settlement/manifest.json",
    "requirements-dev.txt", "tests/conftest.py", "pyproject.toml",
}


def prototype_tests(changed=()):
    """Add changed tests and matching backend tests; unknown backend => full."""
    selected = set(PROTOTYPE)
    for name in changed:
        path = Path(name)
        if name in FULL_GATE or name.startswith("wallet_service/") or not (ROOT / path).exists():
            return ["tests"]
        if name.startswith("tests/") and path.suffix == ".py":
            selected.add(name)
        elif name.startswith("tests/fixtures/"):
            return ["tests"]
        elif name.startswith("custom_components/bsv_settlement/") and path.suffix == ".py":
            matching = f"tests/test_{path.stem}.py"
            if not (ROOT / matching).is_file():
                return ["tests"]
            selected.add(matching)
    return sorted(selected)


def changed_files(ref):
    return subprocess.check_output(
        ["git", "diff", "--name-only", "--no-renames", ref, "HEAD", "--"],
        cwd=ROOT, text=True).splitlines()


def plan(suite, changed=()):
    python = [
        (["-m", "pytest", "-q", "-ra", "--durations=15", "tests"], "."),
        (["-m", "compileall", "-q", "wallet_service", "custom_components", "scripts"], "."),
    ]
    driver = [
        (["npm", "test"], "frontend/driver"),
        (["npm", "run", "build"], "frontend/driver"),
        (["git", "diff", "--exit-code", "--",
          "custom_components/bsv_settlement/frontend/driver"], "."),
    ]
    operator = [
        (["npm", "test"], "frontend"),
        (["npm", "run", "build"], "frontend"),
        (["git", "diff", "--exit-code", "--",
          "custom_components/bsv_settlement/frontend",
          "frontend/bsv-session-review-card.bundle.js",
          "frontend/bsv-budget-card.bundle.js"], "."),
    ]
    return {
        "quick": [(["-m", "pytest", "-q", "-ra", *QUICK], ".")],
        "prototype": [
            (["-m", "pytest", "-q", "-ra", "--durations=10", *prototype_tests(changed)], "."),
            python[1],
        ],
        "python": python, "driver": driver, "operator": operator,
        "all": python + driver + operator,
    }[suite]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", nargs="?", default="prototype",
                        choices=("quick", "prototype", "python", "driver", "operator", "all"))
    parser.add_argument("--list", action="store_true", help="Print commands without running")
    parser.add_argument("--changed-from", help="Git base SHA: add changed areas or fall back to full")
    args = parser.parse_args()
    commands = plan(args.suite, changed_files(args.changed_from) if args.changed_from else ())
    if not args.list and args.suite in ("prototype", "python", "all"):
        # Never silently import-skip HA tests in either development or release.
        import homeassistant.core  # noqa: F401
        import pytest_asyncio  # noqa: F401
    for command, folder in commands:
        command = ([sys.executable] if command[0] == "-m" else []) + command
        print(f"[{folder}] {' '.join(command)}", flush=True)
        if not args.list:
            subprocess.run(command, cwd=ROOT / folder, check=True)


if __name__ == "__main__":
    main()
