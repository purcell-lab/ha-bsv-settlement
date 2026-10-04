"""Keep the acceptance inventory complete and its evidence pointers executable."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def matrix():
    return json.loads((ROOT/"validation/acceptance-matrix.json").read_text())


def test_acceptance_matrix_covers_all_ten_outcomes_without_claiming_completion():
    data = matrix()
    assert data["schema_version"] == 1
    assert len(data["baseline"]) == 40
    assert sum(data["baseline_test_counts"].values()) == 210
    assert [r["id"] for r in data["outcomes"]] == [f"D{i:02}" for i in range(1,11)]
    for row in data["outcomes"]:
        assert row["status"] in {"partial","blocked"}
        assert row["outcome"] and row["remaining"] and row["issues"]
        assert all(type(i) is int and 2 <= i <= 25 for i in row["issues"])


def test_all_acceptance_evidence_points_to_real_test_functions():
    for row in matrix()["outcomes"]:
        for ref in row["test_refs"]:
            path = ROOT/ref["path"]
            assert path.is_relative_to(ROOT) and path.is_file()
            if path.suffix == ".js":
                assert f'test("{ref["test"]}"' in path.read_text()
                continue
            names = {n.name for n in ast.walk(ast.parse(path.read_text()))
                     if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
            assert ref["test"] in names, f"{row['id']}: {ref}"


def test_current_validation_is_separate_from_historical_baseline():
    data = matrix()
    current = data["current_validation"]
    assert len(current["commit"]) == 40 and current["commit"] != data["baseline"]
    assert current["ci"].startswith("https://github.com/purcell-lab/ha-bsv-settlement/actions/runs/")
    assert sum(current["test_counts"].values()) == 871
    assert "not live-payment" in current["scope"]
