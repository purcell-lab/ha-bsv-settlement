"""Deterministic local validation entry point. No live HA/wallet calls.

Use the same dependency versions as CI. Full validation includes every Python
and JavaScript regression plus reproducible build checks. Quick is not release
evidence. Commands are fixed argument arrays, never shell-interpolated input.
"""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
QUICK = (
    "tests/test_distribution.py",
    "tests/test_monthly_allowance.py",
    "tests/test_tariff_overlap.py",
)


def plan(suite):
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
        "python": python, "driver": driver, "operator": operator,
        "all": python + driver + operator,
    }[suite]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=("quick", "python", "driver", "operator", "all"))
    parser.add_argument("--list", action="store_true", help="Print commands without running")
    args = parser.parse_args()
    commands = plan(args.suite)
    if not args.list and args.suite in ("python", "all"):
        # Avoid a misleading green release run that silently skips HA tests.
        import homeassistant.core  # noqa: F401
        import pytest_asyncio  # noqa: F401
    for command, folder in commands:
        command = ([sys.executable] if command[0] == "-m" else []) + command
        print(f"[{folder}] {' '.join(command)}", flush=True)
        if not args.list:
            subprocess.run(command, cwd=ROOT / folder, check=True)


if __name__ == "__main__":
    main()
