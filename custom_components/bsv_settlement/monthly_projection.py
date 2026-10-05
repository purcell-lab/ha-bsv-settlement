"""Read retained monthly ownership without binding, ticking or activating it."""
from types import SimpleNamespace
from urllib.parse import urlparse

from .monthly_authority import MonthlyAuthorities, decode
from .monthly_ownership import KEY
from .pairing import external_origin


def owned_accounts(api, identity):
    if KEY not in api.saved:
        return []
    # Validate signed consent, operator/origin, bindings and ledger together.
    # This ephemeral validator has no adapters and cannot perform any payment.
    service = MonthlyAuthorities(SimpleNamespace(api=api), policies={},
                                 origin=urlparse(external_origin(api.hass)).hostname)
    state = service.snapshot()
    result = []
    for binding in state["bindings"].values():
        authority = state["authorities"][binding["authority_id"]]
        if authority["terms"]["driver_identity"] != identity:
            continue
        ledger = decode(authority["ledger"])
        result.append((binding, ledger))
    return result
