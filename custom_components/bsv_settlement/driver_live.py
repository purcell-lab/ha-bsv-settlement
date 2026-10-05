"""Read-only, owner-scoped OCPP display; never a settlement or control input."""
from datetime import datetime

from .const import DOMAIN
from .session_review import now

STATUSES = {"Available", "Preparing", "Charging", "SuspendedEV", "SuspendedEVSE",
            "Finishing", "Reserved", "Unavailable", "Faulted", "Occupied"}


def ocpp_display(api, proxy_id, record):
    missing = {"available": False, "status": None, "reason": "OCPP status unavailable"}
    proxy = api.hass.data.get(DOMAIN, {}).get(proxy_id)
    latest = (getattr(proxy, "data", None) or {}).get("latest_session")
    if (not record or record.get("ended_at") or not latest
            or latest.get("session_id") != record.get("session_id")):
        return missing | {"reason": "No current session linked to this driver page"}
    observers = [c for c in api.hass.data.get(DOMAIN, {}).values()
                 if getattr(c, "mode", None) == "ocpp_import_shadow"
                 and (getattr(c, "data", None) or {}).get("recorder", {}).get("legacy_entry_id") == proxy_id]
    if len(observers) != 1:
        return missing | {"reason": "No unique OCPP observer linked to this recorder"}
    observer = observers[0]
    try:
        from .ocpp_shadow import source_binding
        if (observer.binding_error or source_binding(api.hass, observer.sources)
                != observer.entry.data["source_binding"]):
            return missing
        checked = datetime.fromisoformat(observer.data["updated_at"])
        if not checked.tzinfo or not 0 <= (now() - checked).total_seconds() <= 60:
            return missing | {"reason": "OCPP observer update is stale"}
        state = api.hass.states.get(observer.sources["status"])
        if state is None or state.attributes.get("restored") or state.state not in STATUSES:
            return missing
        return {"available": True, "status": state.state, "checked_at": checked.isoformat(),
                "reason": "Current OCPP connector state. Separate from payment status and energy direction."}
    except (KeyError, TypeError, ValueError, AttributeError):
        return missing
