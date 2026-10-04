"""Overlapping source estimates must not erase unrelated, priced energy."""
from copy import deepcopy
from decimal import Decimal
from itertools import permutations

import pytest

from custom_components.bsv_settlement.proxy_ledger import (
    build_records, select_prices, instant, duration,
)


def t(minute):
    return f"2026-10-04T20:{minute:02d}:00+10:00"


def price(start, end, rate, observed=0, final=True):
    return {"t": t(observed), "start": t(start), "end": t(end),
            "value": str(rate), "unit": "$/kWh", "estimate": not final}


def history(prices):
    return {
        "state": [{"t": t(0), "value": "Idle"},
                  {"t": t(5), "value": "Charging"},
                  {"t": t(15), "value": "Ended"}],
        "import": [{"t": t(0), "value": "1", "unit": "MWh"},
                   {"t": t(15), "value": "1.001", "unit": "MWh"}],
        "export": [{"t": t(0), "value": "2", "unit": "MWh"}],
        "import_price": prices, "export_price": [],
    }


def record(prices):
    return build_records(history(prices), "sensor.test_state", t(40))[0]


def test_final_five_minute_rates_override_broad_estimate_without_double_counting():
    rows = [price(0, 30, ".9", 20, False),
            price(5, 10, ".2", 6), price(10, 15, ".4", 11)]
    original = deepcopy(rows)
    result = record(rows)
    assert result["net_cost_aud"] == .30
    assert result["import_kwh"] == 1
    assert Decimal(result["unpriced_import_wh"]) == 0
    assert Decimal(result["estimated_rate_import_wh"]) == 0
    assert not any("tariff" in f for f in result["quality_flags"])
    spans, issues = select_prices(rows)
    assert not issues
    assert all(a["end"] <= b["start"] for a, b in zip(spans, spans[1:]))
    assert sum(duration(p["start"], p["end"]) for p in spans) == 1800
    assert rows == original


def test_actual_live_boundary_pattern_is_order_independent():
    rows = [price(30, 59, ".3461835", 29, False),
            price(30, 35, ".3572403", 34),
            price(35, 40, ".3592562", 39)]
    expected = select_prices(rows)
    for order in permutations(rows):
        assert select_prices(order) == expected
    spans, _ = expected
    assert [p["rate"] for p in spans] == list(map(
        Decimal, [".3572403", ".3592562", ".3461835"]))
    assert [p["final"] for p in spans] == [True, True, False]


def test_unreplaced_estimate_is_disclosed_not_silently_final():
    r = record([price(0, 30, ".9", 20, False), price(5, 10, ".2", 6)])
    assert r["net_cost_aud"] == .55
    assert Decimal(r["estimated_rate_import_wh"]) == 500
    assert "import:estimated_tariff" in r["quality_flags"]


def test_same_bounds_final_revision_wins_even_over_later_estimate():
    r = record([price(5, 15, ".1", 5), price(5, 15, ".3", 10),
                price(5, 15, ".9", 20, False)])
    assert r["net_cost_aud"] == .30


def test_newest_estimate_wins_overlap_and_tied_time_prefers_specificity():
    spans, _ = select_prices([price(0, 30, ".1", 5, False),
                             price(5, 15, ".2", 10, False)])
    assert [p["rate"] for p in spans] == list(map(Decimal, [".1", ".2", ".1"]))
    assert record([price(0, 30, ".1", 10, False),
                   price(5, 15, ".2", 10, False)])["net_cost_aud"] == .2


@pytest.mark.parametrize("final", [True, False])
def test_equal_priority_conflicting_duplicates_are_ambiguous(final):
    rows = [price(5, 15, ".1", 10, final), price(5, 15, ".2", 10, final)]
    for order in permutations(rows):
        r = record(order)
        assert r["net_cost_aud"] is None
        assert Decimal(r["unpriced_import_wh"]) == 1000
        assert "import:overlapping_tariff_periods" in r["quality_flags"]


def test_conflicting_final_bounds_quarantine_only_affected_energy():
    rows = [price(0, 10, ".1", 10), price(5, 15, ".2", 15)]
    r = record(rows)
    assert r["net_cost_aud"] is None
    assert Decimal(r["unpriced_import_wh"]) == 500
    assert "import:overlapping_tariff_periods" in r["quality_flags"]
    spans, _ = select_prices(rows)
    assert [p["rate"] for p in spans] == [Decimal(".1"), None, Decimal(".2")]


def test_later_conflicts_do_not_erase_previous_session():
    r = record([price(0, 15, ".1"), price(20, 30, ".2"), price(25, 35, ".3")])
    assert r["net_cost_aud"] == .1
    assert not any("tariff" in f for f in r["quality_flags"])


def test_gap_is_not_zero_or_filled_by_latest_price():
    r = record([price(5, 10, ".2"), price(11, 15, ".3")])
    assert r["net_cost_aud"] is None
    assert Decimal(r["unpriced_import_wh"]) == 100
    assert "import:missing_tariff" in r["quality_flags"]


def test_equal_final_rates_can_overlap_and_negative_rates_are_preserved():
    r = record([price(0, 10, "-.2"), price(5, 15, "-.2")])
    assert r["net_cost_aud"] == -.2
    assert Decimal(r["unpriced_import_wh"]) == 0


def test_export_credit_and_negative_export_use_same_selection_rules():
    h = history([price(0, 30, ".1")])
    h["state"][1]["value"] = "Discharging"
    h["import"] = h["import"][:1]
    h["export"].append({"t": t(15), "value": "2.001", "unit": "MWh"})
    for rate, expected in [(".2", -.2), ("-.2", .2)]:
        h["export_price"] = [price(0, 30, ".9", 20, False),
                              price(5, 10, rate, 6), price(10, 15, rate, 11)]
        r = build_records(h, "sensor.test_state", t(40))[0]
        assert r["net_cost_aud"] == expected
        assert r["export_kwh"] == 1
        assert Decimal(r["unpriced_export_wh"]) == 0


def test_malformed_rows_are_skipped_without_crashing():
    valid = price(5, 15, ".2")
    rows = [dict(valid, value="NaN"), dict(valid, t="bad"),
            dict(valid, start="2026-10-04T20:05:00"),
            dict(valid, unit="c/kWh"), dict(valid, end=t(4)), valid]
    spans, issues = select_prices(rows)
    assert not issues
    assert spans == [{"start": instant(t(5)), "end": instant(t(15)),
                      "rate": Decimal(".2"), "final": True}]
