"""Native read-only Lovelace cards; no entity or settlement configuration writes."""
import copy
import re


def add_live_ocpp(config, entities, *, export_derived=True):
    """Extend the existing observer section, preserving every unrelated card."""
    if any(not re.fullmatch(r"sensor\.[a-z0-9_]+", e) for e in entities.values()):
        raise ValueError("Only explicit sensor entities can be displayed")
    result = copy.deepcopy(config)
    view = next(v for v in result["views"] if v["path"] == "overview")
    sections = view.setdefault("sections", [])
    existing = next((s for s in sections if any(
        c.get("entity") == entities["observer"] for c in s.get("cards", []))), None)
    if existing is None:
        raise ValueError("Existing OCPP observer section not found")
    # Keep original observer/status/energy cards; replace only our own live panels.
    existing["cards"] = [c for c in existing["cards"] if c.get("title") not in (
        "OCPP connector and live readings", "OCPP energy and session",
        "OCPP signed power (last 2 hours)", "OCPP telemetry limits")]

    def rows(items):
        return [{"entity": entities[key], "name": name, "secondary_info": "last-updated"}
                for key, name in items]

    existing["cards"].extend([
        {"type": "entities", "title": "OCPP connector and live readings",
         "show_header_toggle": False, "grid_options": {"columns": 12, "rows": "auto"},
         "entities": rows([
             ("connector", "Connector state"), ("transaction", "Native transaction ID (0 = none)"),
             ("power", "Signed power: + charging / − V2G"),
             ("current", "Reported import current"), ("voltage", "Reported voltage"),
             ("soc", "Vehicle state of charge"), ("error", "Connector error")])},
        {"type": "entities", "title": "OCPP energy and session",
         "show_header_toggle": False, "grid_options": {"columns": 12, "rows": "auto"},
         "entities": rows([
             ("session_energy", "Reported session import"),
             ("export_energy", "Estimated export from negative power" if export_derived
              else "Export register (source not verified)"),
             ("import_register", "Cumulative import register"),
             ("meter_start", "Transaction starting import register"),
             ("duration", "Reported session duration")])},
        {"type": "history-graph", "title": "OCPP signed power (last 2 hours)",
         "hours_to_show": 2, "entities": [entities["power"]],
         "grid_options": {"columns": "full", "rows": 4}},
        {"type": "markdown", "title": "OCPP telemetry limits",
         "grid_options": {"columns": "full", "rows": "auto"},
         "entity_id": [entities["observer"], entities["export_energy"]],
         "content": (
             "{% set s = state_attr('" + entities["observer"] + "', 'current_span') %}"
             "{% set p = state_attr('" + entities["observer"] + "', 'previous_span') %}"
             "Observer native transaction: **{{ s.native_transaction_id if s else "
             "'none active' }}**. "
             "{% if p %}Last observed native ID: **{{ p.native_transaction_id }}** "
             "(historical partial span).{% endif %}\n\n"
             "Export entity source: `{{ state_attr('" + entities["export_energy"] +
             "', 'source') or 'not provided' }}`. "
             "Derived export is an estimate, not a charger-reported export register; "
             "its reset period is not assumed to match the session import figure.\n\n"
             "During tested V2G, signed import power became negative while import "
             "current stayed at 0 A and connector state remained Charging. "
             "State/current alone do not identify direction. Values may remain after "
             "a session ends; check their timestamps. Unknown readings are not zero.\n\n"
             "**Settlement source remains Sigenergy.** These cards cannot control "
             "charging, change consent or trigger payments.")},
    ])
    return result
