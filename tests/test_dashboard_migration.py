"""Fictional configuration only; no HA writes or private installation IDs."""
from copy import deepcopy
from frontend.dashboard import cards, redesign
import pytest


def legacy():
    common = {"config_entry_id":"fictional-wallet", "proxy_config_entry_id":"fictional-proxy",
              "wallet_entity":"sensor.fictional_wallet", "proxy_entity":"sensor.fictional_proxy",
              "rate_entity":"sensor.fictional_rate"}
    return {"views":[{"path":"sessions","cards":[
        {"type":"custom:bsv-budget-card", **common},
        {"type":"custom:bsv-session-review-card", **common},
        {"type":"custom:bsv-receive-qr-card","entity":"sensor.fictional_receiving"},
        {"type":"tile","entity":"sensor.fictional_confirmed_wallet_balance"},
        {"type":"button","entity":"text.fictional_driver_identity"},
        {"type":"button","tap_action":{"perform_action":"bsv_settlement.wallet_self_test"}},
        {"type":"tile","entity":"sensor.fictional_testnet_operator_wallet_status"},
        {"type":"markdown","content":"Last saved offline self-test: fictional"},
        {"type":"tile","entity":"sensor.bsv_settlement_mock_fictional"},
    ]}]}


def test_legacy_then_repeat_is_idempotent_without_mutating_input():
    initial = legacy()
    original = deepcopy(initial)
    first = redesign(initial)
    assert initial == original
    assert [v["path"] for v in first["views"]] == [
        "overview","drivers","payments","operator-credits","wallet","testing"]
    assert redesign(first) == first
    assert any(c.get("type")=="custom:bsv-receive-qr-card" for c in cards(first))
    assert any(c.get("title")=="Manual-payment recipient" for c in cards(first))


def test_repeat_preserves_user_cards_extra_views_metadata_and_order():
    current = redesign(legacy())
    current["title"] = "Fictional operator dashboard"
    current["views"][1]["sections"][0]["cards"].append(
        {"type":"markdown","content":"Operator-added instructions"})
    current["views"].append({"path":"custom","type":"sections","sections":[]})
    current["views"].reverse()
    before = deepcopy(current)
    assert redesign(current) == before
    assert current == before


@pytest.mark.parametrize("problem",["missing_view","duplicate_view","wrong_view_type","missing_card"])
def test_partial_redesign_refuses_destructive_rebuild(problem):
    current = redesign(legacy())
    if problem == "missing_view":
        current["views"].pop()
    elif problem == "duplicate_view":
        current["views"].append(deepcopy(current["views"][0]))
    elif problem == "wrong_view_type":
        current["views"][0]["type"] = "masonry"
    else:
        current["views"][4]["sections"][1]["cards"].pop(0)
    before = deepcopy(current)
    with pytest.raises(ValueError,match="Partially redesigned"):
        redesign(current)
    assert current == before


def test_repeat_repairs_only_budget_wallet_reference():
    current = redesign(legacy())
    budget = next(c for c in cards(current) if c.get("type")=="custom:bsv-budget-card")
    budget.pop("wallet_entity")
    expected = deepcopy(current)
    next(c for c in cards(expected) if c.get("type")=="custom:bsv-budget-card")[
        "wallet_entity"] = "sensor.fictional_wallet"
    assert redesign(current) == expected
    assert "wallet_entity" not in budget


def test_recipient_tabs_keep_routes_directions_and_match_full_width():
    initial = redesign(legacy())
    expected = {
        "payments": ("Owner credits", "driver_to_operator", "Driver → Owner"),
        "operator-credits": ("Driver credits", "operator_to_driver", "Owner → Driver"),
    }
    for view in initial["views"]:
        if view["path"] not in expected:
            continue
        title, direction, arrow = expected[view["path"]]
        view["title"] = "Old title"
        view["sections"][0].pop("column_span")
        review = next(c for c in cards(view) if c.get("type") == "custom:bsv-session-review-card")
        review["grid_options"] = {"columns": 12, "rows": 5, "custom": "preserved"}
    original = deepcopy(initial)
    migrated = redesign(initial)
    assert initial == original
    assert [v["path"] for v in migrated["views"]] == [v["path"] for v in initial["views"]]
    for view in migrated["views"]:
        if view["path"] not in expected:
            continue
        title, direction, arrow = expected[view["path"]]
        assert view["title"] == title
        assert arrow in view["header"]["card"]["content"]
        assert view["sections"][0]["column_span"] == 2
        review = next(c for c in cards(view) if c.get("type") == "custom:bsv-session-review-card")
        assert review["direction"] == direction
        assert review["grid_options"] == {"columns": "full", "rows": "auto", "custom": "preserved"}
    assert redesign(migrated) == migrated
