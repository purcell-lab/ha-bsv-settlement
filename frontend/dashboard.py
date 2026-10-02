"""Rebuild the existing five-view HA dashboard without embedding site identities."""
from copy import deepcopy


def cards(tree):
    if isinstance(tree, dict):
        if "type" in tree:
            yield tree
        for value in tree.values():
            yield from cards(value)
    elif isinstance(tree, list):
        for value in tree:
            yield from cards(value)


def redesign(config):
    """Derive site IDs from the existing dashboard; preserve all stable paths."""
    old = deepcopy(config)
    flat = list(cards(old))
    budget = next(c for c in flat if c.get("type") == "custom:bsv-budget-card")
    review = next(c for c in flat if c.get("type") == "custom:bsv-session-review-card")
    qr = next(c for c in flat if c.get("type") == "custom:bsv-receive-qr-card")
    balance = next(c["entity"] for c in flat if str(c.get("entity", "")).endswith("_confirmed_wallet_balance"))
    operator = {"type": "custom:bsv-operator-card", "wallet_entity": review["wallet_entity"],
                "proxy_entity": review["proxy_entity"], "balance_entity": balance,
                "config_entry_id": review["config_entry_id"]}

    def sized(card):
        return deepcopy(card) | {"grid_options": {"columns": 12, "rows": "auto"}}

    def section(*items):
        return {"type": "grid", "cards": [sized(c) for c in items]}

    def view(title, path, icon, intro, sections):
        return {"title": title, "path": path, "icon": icon, "type": "sections",
                "max_columns": 2, "subview": False, "header": {"layout": "responsive",
                "card": {"type": "markdown", "text_only": True, "content": intro}}, "sections": sections}

    manual_entities = [c["entity"] for c in flat if c.get("type") == "button" and
                       str(c.get("entity", "")).startswith("text.") and "driver" in c["entity"]]
    manual = {"type": "entities", "title": "Manual-payment recipient",
              "show_header_toggle": False, "entities": manual_entities}
    operator_key = {"type": "markdown", "entity_id": [review["wallet_entity"]], "content":
        "### Operator public identity\n"
        "{% set e = '" + review["wallet_entity"] + "' %}\n"
        "{% if states(e) not in ['unknown','unavailable'] %}\n"
        "`{{ state_attr(e, 'operator_public_key') }}`\n"
        "{% else %}Wallet unavailable. Identity withheld.{% endif %}\n\n"
        "Public identity only. Protect the local wallet backup before funding. "
        "The embedded hot-wallet key is not hardware protected."}
    rate = {"type": "entities", "title": "Demonstration conversion",
            "show_header_toggle": False, "entities": [
                {"entity": "input_number.bsv_conversion_rate_setting", "name": "Set sat per AUD"},
                {"entity": review["rate_entity"], "name": "Current conversion"}]}
    offline_button = next(c for c in flat if c.get("type") == "button" and
                          c.get("tap_action", {}).get("perform_action") == "bsv_settlement.wallet_self_test")
    offline_tile = next(c for c in flat if c.get("type") == "tile" and
                        "testnet_operator_wallet_status" in c.get("entity", ""))
    offline_result = next(c for c in flat if c.get("type") == "markdown" and
                          "Last saved offline self-test" in c.get("content", ""))
    mock_entities = list(dict.fromkeys(c["entity"] for c in flat
                                     if str(c.get("entity", "")).startswith("sensor.bsv_settlement_mock")))
    return {"views": [
        view("Overview", "overview", "mdi:ev-station", "## Charging & settlement\nThe latest session, its next action and the operator's funds.",
             [section(operator), section(operator | {"mode": "wallet"})]),
        view("Drivers", "drivers", "mdi:account-check-outline", "## Set up a driver\nOne approval belongs to one session. Never reuse a previous driver's approval.",
             [section(budget), section({"type": "markdown", "content":
                "### Before the session ends\n\n"
                "1. Share a private link with the intended driver.\n"
                "2. Ask them to approve in BSV Browser.\n"
                "3. Match the approval to the correct session.\n"
                "4. Check that the receiving wallet is registered.\n\n"
                "Driver charges need the open browser. Operator credits can run with it closed."},
                manual, {"type": "markdown", "content":
                "Manual receiving details are **not** the session-specific BRC-29 wallet key. "
                "Independently confirm the address before a manual credit. Never enter a seed or private key."})]),
        view("Payments", "payments", "mdi:swap-horizontal", "## Settle a session\nAutomatic credits and manual exceptions, with exact amounts and transaction status.",
             [section(review)]),
        view("Wallet", "wallet", "mdi:wallet-outline", "## Operator wallet\nConfirmed funds and pending change are different. Check BSV mainnet before funding.",
             [section(operator | {"mode": "wallet"}), section(qr, operator_key)]),
        view("Diagnostics", "testing", "mdi:tune", "## Settings & diagnostics\nKeep fictional tests separate from real mainnet settlements.",
             [section(rate, {"type": "markdown", "content":
                "The conversion is a demonstration rate, not market FX. Existing approvals and reviews keep their frozen rate."}),
              section(offline_tile, offline_button, offline_result),
              section({"type": "entities", "title": "Fictional mock results", "entities": mock_entities,
                       "show_header_toggle": False},
                      {"type": "markdown", "content": "Mock and offline tests do not move money. Their results are not proof of live payment readiness."})]),
    ]}
