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
    """Migrate the legacy layout once; preserve an already redesigned layout."""
    old = deepcopy(config)
    flat = list(cards(old))
    budget = next(c for c in flat if c.get("type") == "custom:bsv-budget-card")
    review = next(c for c in flat if c.get("type") == "custom:bsv-session-review-card")
    budget["wallet_entity"] = review["wallet_entity"]
    if any(c.get("type") == "custom:bsv-operator-card" for c in flat):
        required = {
            "overview": "custom:bsv-operator-card",
            "drivers": "custom:bsv-budget-card",
            "payments": "custom:bsv-session-review-card",
            "wallet": "custom:bsv-receive-qr-card",
            "testing": None,
        }
        for path, kind in required.items():
            views = [v for v in old.get("views", []) if v.get("path") == path]
            if (len(views) != 1 or views[0].get("type") != "sections" or
                    (kind and not any(c.get("type") == kind for c in cards(views[0])))):
                raise ValueError("Partially redesigned dashboard; review its backup before migration")
        # Keep native controls, user cards, private config, metadata and extra
        # views intact, except managed settlement labels, sizing, wallet link
        # and the removed testnet/mock diagnostics cards.
        return split_settlement_views(drop_removed_backend_cards(old))
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
    return split_settlement_views({"views": [
        view("Overview", "overview", "mdi:ev-station", "## Charging & settlement\nThe latest session, its next action and the operator's funds.",
             [section(operator), section(operator | {"mode": "wallet"})]),
        view("Drivers", "drivers", "mdi:account-check-outline", "## Set up a driver\nOne approval belongs to one session. Never reuse a previous driver's approval.",
             [section(budget), section({"type": "markdown", "content":
                "### Before the session ends\n\n"
                "1. Share a private link with the intended driver.\n"
                "2. Ask them to approve in BSV Browser.\n"
                "3. Match the approval to the correct session.\n"
                "4. Check that the receiving wallet is registered.\n\n"
                "Driver charges need the open browser. Driver credits can run with it closed."},
                manual, {"type": "markdown", "content":
                "Manual receiving details are **not** the session-specific BRC-29 wallet key. "
                "Independently confirm the address before a manual credit. Never enter a seed or private key."})]),
        view("Payments", "payments", "mdi:swap-horizontal", "## Settle a session\nAutomatic credits and manual exceptions, with exact amounts and transaction status.",
             [section(review)]),
        view("Wallet", "wallet", "mdi:wallet-outline", "## Operator wallet\nConfirmed funds and pending change are different. Check BSV mainnet before funding.",
             [section(operator | {"mode": "wallet"}), section(qr, operator_key)]),
        view("Diagnostics", "testing", "mdi:tune", "## Settings & diagnostics\nKeep fictional tests separate from real mainnet settlements.",
             [section(rate, {"type": "markdown", "content":
                "The conversion is a demonstration rate, not market FX. Existing approvals and reviews keep their frozen rate."})]),
    ]})


def removed_backend_card(card):
    """Diagnostics cards for the removed testnet wallet and mock backends."""
    entity = str(card.get("entity", ""))
    content = card.get("content") if isinstance(card.get("content"), str) else ""
    return ("_testnet_operator_wallet_status" in entity or entity.startswith("sensor.bsv_settlement_mock")
            or card.get("tap_action", {}).get("perform_action") == "bsv_settlement.wallet_self_test"
            or "_testnet_operator_wallet_status" in content or "Last saved offline self-test" in content
            or "Mock and offline tests do not move money" in content
            or (card.get("type") == "entities" and card.get("title") == "Fictional mock results"))


def drop_removed_backend_cards(config):
    """Remove those cards, and sections they leave empty, from the Diagnostics view only."""
    result = deepcopy(config)
    for view in result.get("views", []):
        if view.get("path") != "testing" or not isinstance(view.get("sections"), list):
            continue
        kept = []
        for section in view["sections"]:
            items = section.get("cards") if isinstance(section, dict) else None
            if isinstance(items, list) and any(isinstance(c, dict) and removed_backend_card(c) for c in items):
                section["cards"] = [c for c in items if not (isinstance(c, dict) and removed_backend_card(c))]
                if not section["cards"]:
                    continue
            kept.append(section)
        view["sections"] = kept
    return result


def split_settlement_views(config):
    """Keep existing paths/user cards; split the managed settlement card only."""
    result = deepcopy(config)
    views = result["views"]
    payment_views = [v for v in views if v.get("path") == "payments"]
    operator_views = [v for v in views if v.get("path") == "operator-credits"]
    if len(payment_views) != 1 or len(operator_views) > 1:
        raise ValueError("Ambiguous settlement views; review before migration")
    driver = payment_views[0]
    review_cards = [c for c in cards(driver) if c.get("type") == "custom:bsv-session-review-card"]
    if len(review_cards) != 1:
        raise ValueError("Ambiguous settlement cards; review before migration")
    review = review_cards[0]
    review["direction"] = "driver_to_operator"
    if operator_views:
        existing = [c for c in cards(operator_views[0]) if c.get("type") == "custom:bsv-session-review-card"]
        if len(existing) != 1 or existing[0].get("direction") != "operator_to_driver":
            raise ValueError("Existing operator-credit view is not a managed split view")
    else:
        views.insert(views.index(driver) + 1, {
            "title": "Operator credits", "path": "operator-credits", "icon": "mdi:cash-minus",
            "type": "sections", "max_columns": 2, "subview": False,
            "header": {"layout": "responsive", "card": {"type": "markdown", "text_only": True,
                "content": "## Operator credits\nMoney paid to drivers. Check receiving registrations, funding and confirmation."}},
            "sections": [{"type": "grid", "column_span": 2, "cards": [
                deepcopy(review) | {"direction": "operator_to_driver",
                                    "grid_options": {"columns": "full", "rows": "auto"}}]}]})
    for view in views:
        if view.get("path") == "overview":
            view["title"] = "Status"
        managed = {
            "payments": ("Owner credits", "Driver → Owner. Money received by the owner. Review consent, amounts, fees and collection status."),
            "operator-credits": ("Driver credits", "Owner → Driver. Money received by the driver. Check receiving registrations, funding and confirmation."),
        }.get(view.get("path"))
        if managed:
            view["title"] = managed[0]
            header = view.setdefault("header", {})
            header.setdefault("layout", "responsive")
            header.setdefault("card", {}).update({
                "type": "markdown", "text_only": True,
                "content": f"## {managed[0]}\n{managed[1]}",
            })
            for section in view.get("sections", []):
                managed_cards = [c for c in cards(section)
                                 if c.get("type") == "custom:bsv-session-review-card"]
                if managed_cards:
                    section["column_span"] = 2
                    for card in managed_cards:
                        card.setdefault("grid_options", {}).update(
                            {"columns": "full", "rows": "auto"})
        if view.get("path") == "drivers":
            for card in cards(view):
                if card.get("type") == "markdown" and isinstance(card.get("content"), str):
                    card["content"] = card["content"].replace(
                        "policy in Operator credits", "policy in Driver credits"
                    ).replace("Operator credits can run", "Driver credits can run")
    return result
