"""Local metadata checks; the GitHub workflow also runs official HACS validation."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_hacs_metadata_and_single_integration():
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["name"] == "BSV Settlement (Mock PoC)"
    assert hacs["content_in_root"] is False
    assert hacs["homeassistant"] == "2026.9.4"
    domains = [p.name for p in (ROOT / "custom_components").iterdir()
               if p.is_dir() and not p.name.startswith((".", "__"))]
    assert domains == ["bsv_settlement"]


def test_manifest_distribution_requirements():
    manifest = json.loads((ROOT / "custom_components/bsv_settlement/manifest.json").read_text())
    for key in ("domain", "documentation", "issue_tracker", "codeowners", "name", "version"):
        assert manifest[key]
    assert manifest["version"] == "0.1.1"
    assert manifest["domain"] == "bsv_settlement"
    assert manifest["config_flow"] is True
    assert manifest["requirements"] == []


def test_brand_and_translations():
    root_icon = (ROOT / "brand/icon.png").read_bytes()
    assert root_icon[:8] == b"\x89PNG\r\n\x1a\n"
    assert root_icon == (ROOT / "custom_components/bsv_settlement/brand/icon.png").read_bytes()
    strings = json.loads((ROOT / "custom_components/bsv_settlement/strings.json").read_text())
    english = json.loads((ROOT / "custom_components/bsv_settlement/translations/en.json").read_text())
    assert strings == english
