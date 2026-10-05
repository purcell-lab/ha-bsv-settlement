"""Retained HA accounts for monthly settlement; no ownership or activation.

Only operator-configured sensor_proxy recorders are eligible. This adapter never
selects the newest session as a substitute and never refreshes signing authority.
"""
import copy
from datetime import datetime, timezone

from .api import WalletError
from .const import DOMAIN
from .monthly_consent import timestamp
from .session_review import account_snapshot, digest


class RetainedSessionAccounts:
    """Resolve a previously verified binding against the actual HA recorder.

    ``stations`` maps reviewed station IDs to exact recorder entry IDs. Neither
    this mapping nor a binding may come directly from a browser request. The
    caller must pass the binding restored by MonthlyAuthorities.
    """

    def __init__(self, hass, stations, *, clock=None):
        if (not isinstance(stations, dict) or not stations
                or any(not isinstance(k, str) or not k
                       or not isinstance(v, str) or not v for k, v in stations.items())
                or len(set(stations.values())) != len(stations)):
            raise WalletError("Use an unambiguous reviewed station/recorder mapping")
        self.hass = hass
        self.stations = dict(stations)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def __call__(self, binding):
        try:
            entry, session_id = binding["account_key"].split("|")
            if (not entry or not session_id
                    or self.stations.get(binding["station_id"]) != entry):
                raise ValueError()
            expected_tx = binding["transaction_id"]
            expected_open = binding["opened_at"]
        except (KeyError, TypeError, ValueError, AttributeError):
            raise WalletError("Monthly account does not match the reviewed recorder") from None
        recorder = self.hass.data.get(DOMAIN, {}).get(entry)
        if recorder is None or getattr(recorder, "mode", None) != "sensor_proxy":
            raise WalletError("Authoritative session recorder is unavailable")
        try:
            await recorder.async_request_refresh()
        except Exception:
            raise WalletError("Authoritative session recorder refresh failed") from None
        # A coordinator can retain stale data after a failed refresh.
        if (self.hass.data.get(DOMAIN, {}).get(entry) is not recorder
                or getattr(recorder, "last_update_success", False) is not True):
            raise WalletError("Authoritative session recorder is stale or replaced")
        data = recorder.data
        archive = getattr(recorder, "archive", None)
        if (not isinstance(data, dict) or data.get("issues")
                or not isinstance(archive, list)):
            raise WalletError("Authoritative session history is unavailable")
        candidates = [data.get("latest_session"), data.get("previous_session"), *archive]
        matches = [copy.deepcopy(row) for row in candidates
                   if isinstance(row, dict) and row.get("session_id") == session_id]
        if not matches:
            raise WalletError("Exact monthly session is not in retained history")
        accounts = []
        for record in matches:
            # Existing coverage and quality rules apply equally to all adapters.
            account = account_snapshot(record)
            if (account["ocpp_transaction_id"] != expected_tx
                    or account["opened_at"] != expected_open
                    or not timestamp(expected_open) < timestamp(account["ended_at"]) <= self.clock()):
                raise WalletError("Retained session does not match its immutable binding")
            accounts.append(account)
        if len({digest(account) for account in accounts}) != 1:
            raise WalletError("Conflicting retained session accounts require reconciliation")
        return matches[0]
