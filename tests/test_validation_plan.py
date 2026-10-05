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
