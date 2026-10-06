"""Production session accounts must equal an independently written reference.

The reference (tests/golden/reference_calculator.py) imports nothing from the
integration. It evaluates each second exactly with rational arithmetic; the
production ledger sweeps intervals with 28-digit Decimal arithmetic. They must
agree to the cent and satoshi under ROUND_HALF_UP, and their unrounded amounts
must agree far below a cent.
"""
import ast
from decimal import Decimal
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.bsv_settlement.proxy_ledger import build_records

GOLDEN = Path(__file__).parent / "golden"
FIXTURES = sorted((GOLDEN / "fixtures").glob("*.json"))
spec = importlib.util.spec_from_file_location("reference_calculator", GOLDEN / "reference_calculator.py")
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


@pytest.fixture(autouse=True)
def no_network_session(monkeypatch):
    monkeypatch.setattr("custom_components.bsv_settlement.mainnet.async_get_clientsession",
                        lambda hass: None)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def production(fixture):
    records = build_records(fixture["observations"], fixture["state_entity"], fixture["as_of"])
    assert len(records) == 1, "each golden fixture holds exactly one session"
    return records[0]


def frac(text):
    numerator, denominator = text.split("/")
    return Fraction(int(numerator), int(denominator))


def test_fixture_set_covers_required_cases():
    covered = {case for path in FIXTURES for case in load(path)["covers"]}
    assert {"debit", "credit", "zero_account", "v2g_export", "mixed_direction",
            "negative_import_price", "negative_export_price", "zero_price",
            "tariff_boundary_mid_sample", "dst_start", "dst_end",
            "interval_straddles_dst_change", "interval_straddles_midnight",
            "overlapping_intervals", "estimated_tariff_warning",
            "rounding_half_cent_debit", "rounding_half_cent_credit"} <= covered
    assert len(FIXTURES) >= 10
    assert all(load(path)["fictional"] is True for path in FIXTURES)


def test_reference_rejects_ambiguous_inputs():
    with pytest.raises(ValueError):
        reference.epoch("2026-10-04T01:00:00")  # no offset
    with pytest.raises(ValueError):
        reference.epoch("2026-10-04T01:00:00.5+10:00")
    assert reference.round_half_up(Fraction(-1, 8), Fraction(1, 100)) == Fraction(-13, 100)
    assert reference.round_half_up(Fraction(1, 8), Fraction(1, 100)) == Fraction(13, 100)


def test_reference_independence():
    """Standard library only; no production module and no Decimal context."""
    tree = ast.parse((GOLDEN / "reference_calculator.py").read_text(encoding="utf-8"))
    modules = {alias.name.split(".")[0] for node in ast.walk(tree)
               if isinstance(node, ast.Import) for alias in node.names}
    modules |= {node.module.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)}
    assert modules == {"datetime", "fractions", "functools", "json", "sys"}


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_reference_reproduces_recorded_expectation(path):
    fixture = load(path)
    assert reference.calculate(fixture) == fixture["expected"]


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_production_account_equals_independent_reference(path):
    from custom_components.bsv_settlement.session_review import account_snapshot
    fixture = load(path)
    expected = fixture["expected"]
    record = production(fixture)
    assert record["opened_at"] == expected["opened_at"]
    assert record["ended_at"] == expected["ended_at"]
    tolerance = Fraction(1, 10 ** 18)
    for field, key in (("import_cost_aud", "import_cost_aud_exact"),
                       ("export_credit_aud", "export_credit_aud_exact"),
                       ("net_cost_aud_unrounded", "net_aud_exact")):
        assert abs(Fraction(Decimal(record[field])) - frac(expected[key])) <= tolerance, field
    for field in ("unpriced_import_wh", "unpriced_export_wh",
                  "estimated_rate_import_wh", "estimated_rate_export_wh"):
        assert abs(Fraction(Decimal(record[field])) - frac(expected[field])) <= tolerance, field
    assert Fraction(Decimal(str(record["import_kwh"]))) * 1000 == frac(expected["import_wh"])
    assert Fraction(Decimal(str(record["export_kwh"]))) * 1000 == frac(expected["export_wh"])
    account = account_snapshot(record)
    assert account["net_amount_aud"] == expected["net_aud"]
    assert Decimal(str(record["net_cost_aud"])) == Decimal(expected["net_aud"])
    estimated = {d for d in ("import", "export") if frac(expected[f"estimated_rate_{d}_wh"])}
    assert {f"{d}:estimated_tariff" for d in estimated} == {
        f for f in record["quality_flags"] if f.endswith(":estimated_tariff")}


def test_exact_half_cent_ties_round_away_from_zero_in_production():
    debit = production(load(GOLDEN / "fixtures" / "g08_half_cent_debit_rounding.json"))
    credit = production(load(GOLDEN / "fixtures" / "g09_half_cent_credit_rounding.json"))
    assert Decimal(debit["net_cost_aud_unrounded"]) == Decimal("0.125")
    assert Decimal(credit["net_cost_aud_unrounded"]) == Decimal("-0.125")
    assert (debit["net_cost_aud"], credit["net_cost_aud"]) == (0.13, -0.13)


@pytest.mark.asyncio
async def test_production_review_direction_and_sats_equal_reference(tmp_path):
    pytest.importorskip("homeassistant")
    from test_mainnet import setup_wallet
    from custom_components.bsv_settlement.session_review import account_snapshot, digest
    from custom_components.bsv_settlement.tariff_provenance import (
        TariffProvenanceLedger, capture, summary)
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        for index, path in enumerate(FIXTURES):
            fixture = load(path)
            expected = fixture["expected"]
            record = production(fixture)
            ledger = TariffProvenanceLedger()
            sources = {"import_price": "sensor.fictional_import_price",
                       "export_price": "sensor.fictional_export_price"}
            found = capture(fixture["observations"], [record], sources)
            ledger.observe(record["session_id"], found[record["session_id"]],
                           digest(account_snapshot(record)), "2026-10-12T00:00:00+00:00")
            proxy_id = f"proxy-{index}"
            hass.data.setdefault("bsv_settlement", {})[proxy_id] = SimpleNamespace(
                mode="sensor_proxy", archive=[], async_request_refresh=AsyncMock(),
                data={"latest_session": record, "previous_session": None, "issues": []},
                tariff_provenance=lambda sid, d, detail=False, ledger=ledger: (
                    ledger.lookup(sid, d) if detail else summary(ledger.lookup(sid, d))))
            rate_entity = f"sensor.fictional_rate_{index}"
            hass.states.async_set(rate_entity, fixture["sat_per_aud"],
                                  {"unit_of_measurement": "sat/AUD"})
            review = await api.reviews.execute("prepare_session_review", {
                "proxy_config_entry_id": proxy_id, "session_id": record["session_id"],
                "conversion_rate_entity": rate_entity}, "admin")
            assert review["amount_sats"] == expected["amount_sats"], path.stem
            assert review["direction"] == expected["direction"], path.stem
            assert review["account"]["net_amount_aud"] == expected["net_aud"]
            provenance = review["tariff_provenance"]
            assert provenance["status"] == "recorded" and provenance["digest_verified"]
            assert provenance["account_digest"] == digest(review["account"])
    finally:
        await hass.async_stop(force=True)
