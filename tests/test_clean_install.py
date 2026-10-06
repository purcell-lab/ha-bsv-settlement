"""Clean-install packaging checks: what HACS ships, installed into an empty HA config.

The full smoke run starts an isolated interpreter (repository not importable)
against a fresh configuration directory. No network, wallet or chain.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("clean_install_smoke", ROOT / "scripts/clean_install_smoke.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def test_hacs_payload_is_the_single_integration_folder():
    domain, source, info = smoke.hacs_payload()
    assert domain == "bsv_settlement" and source == ROOT / "custom_components" / domain
    assert info["content_in_root"] is False
    manifest = json.loads((source / "manifest.json").read_text())
    assert manifest["config_flow"] is True


def test_stage_copies_only_shipped_files_and_never_overwrites(tmp_path):
    domain, target = smoke.stage(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["custom_components"]
    assert sorted(p.name for p in (tmp_path / "custom_components").iterdir()) == [domain]
    shipped = {p.relative_to(target) for p in target.rglob("*") if p.is_file()}
    assert Path("manifest.json") in shipped and Path("frontend/driver/index.html") in shipped
    assert not any("__pycache__" in p.parts or p.suffix == ".pyc" for p in shipped)
    for rel in smoke.STATIC_PATHS.values():
        assert (target / rel).exists(), rel
    with pytest.raises(SystemExit, match="overwrite"):
        smoke.stage(tmp_path)


@pytest.mark.parametrize("text,expected", [
    ("2026.9.4", (2026, 9, 4)), ("2026.10.0b2", (2026, 10, 0)), ("2026.10.1", (2026, 10, 1))])
def test_version_parsing_for_hacs_minimum(text, expected):
    assert smoke.version_tuple(text) == expected


def test_clean_install_smoke_sets_up_and_unloads():
    pytest.importorskip("homeassistant")
    report = smoke.run()
    from custom_components.bsv_settlement.const import SERVICES
    assert report["entries"] == ["ocpp_import_shadow", "sensor_proxy"]
    assert report["unloaded"] is True
    assert report["entities"]["ocpp_import_shadow"] >= 1 and report["entities"]["sensor_proxy"] >= 1
    assert report["actions"] == len(SERVICES) + 1  # Plus the grouped wallet test action.
    assert "request_payment" not in SERVICES
    assert report["static_paths"] == sorted(smoke.STATIC_PATHS)


def test_missing_shipped_asset_fails_the_smoke_test(tmp_path):
    """The child really serves files from the install, not from the repository."""
    pytest.importorskip("homeassistant")
    _, target = smoke.stage(tmp_path)
    (target / "frontend" / "budget-card.js").unlink()
    completed = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/clean_install_smoke.py"), "--child", str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=600)
    assert completed.returncode != 0
    assert "budget-card.js" in completed.stderr
