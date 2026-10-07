"""Guard the test entry point against omitted suites or weakened build checks."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "validation_plan", Path(__file__).resolve().parents[1] / "scripts/validate.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


def test_all_is_exact_union_of_release_suites():
    assert validation.plan("all") == sum(
        [validation.plan(name) for name in ("python", "driver", "operator")], [])


def test_python_runs_entire_directory_not_a_curated_list():
    command, folder = validation.plan("python")[0]
    assert folder == "."
    assert command[-1] == "tests"
    assert "--durations=15" in command
    assert not any(x in command[2:] for x in ("--ignore", "-k", "-m", "--deselect"))


@pytest.mark.parametrize("suite", ["driver", "operator"])
def test_javascript_checks_tests_build_and_tracked_artifacts(suite):
    commands = [command for command, _ in validation.plan(suite)]
    assert commands[0] == ["npm", "test"]
    assert commands[1] == ["npm", "run", "build"]
    assert commands[2][:4] == ["git", "diff", "--exit-code", "--"]


def test_quick_is_explicit_subset_not_release():
    assert validation.plan("quick")[0][0][-len(validation.QUICK):] == list(validation.QUICK)
    assert validation.plan("quick") != validation.plan("all")


def test_unknown_suite_cannot_silently_pass():
    with pytest.raises(KeyError):
        validation.plan("typo")


def test_prototype_retains_active_payment_safety_and_excludes_dormant_monthly_matrix():
    selected = validation.prototype_tests()
    assert len(selected) == len(set(selected))
    assert all((validation.ROOT / p).is_file() for p in selected)
    for part in ("budget", "weekly_mandate", "portal_registration", "collection",
                 "credit_interruption_matrix", "collection_interruption_matrix",
                 "operator_fees", "receipt_ack", "ledger_checkpoint", "upgrade_rollback"):
        assert f"tests/test_{part}.py" in selected
    assert not any("monthly" in p for p in selected)
    assert validation.plan("prototype")[1] == validation.plan("python")[1]


def test_changed_area_adds_its_backend_regression_without_duplicate_tests():
    chosen = validation.prototype_tests(["custom_components/bsv_settlement/ocpp_shadow.py",
                                         "tests/test_ocpp_shadow.py"])
    assert chosen.count("tests/test_ocpp_shadow.py") == 1
    assert set(validation.PROTOTYPE) <= set(chosen)


@pytest.mark.parametrize("changed", [
    "custom_components/bsv_settlement/mainnet.py",
    "custom_components/bsv_settlement/__init__.py",
    "custom_components/bsv_settlement/new_unmapped_module.py",
    "tests/fixtures/upgrade/unknown.json",
    "tests/deleted_test.py",
])
def test_broad_unknown_or_deleted_area_falls_back_to_full(changed):
    assert validation.prototype_tests([changed]) == ["tests"]


def test_existing_unmapped_backend_also_requires_full_regression():
    unmatched = next(p for p in (validation.ROOT / "custom_components/bsv_settlement").glob("*.py")
                     if str(p.relative_to(validation.ROOT)) not in validation.FULL_GATE
                     and not (validation.ROOT / f"tests/test_{p.stem}.py").exists())
    assert validation.prototype_tests([str(unmatched.relative_to(validation.ROOT))]) == ["tests"]
