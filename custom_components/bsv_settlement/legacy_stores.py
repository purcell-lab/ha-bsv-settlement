"""Guarded removal of stores left by deleted mock/embedded_testnet entries (#114).

Removed-backend entries are refused at setup and their stores left in place; the
testnet operator key stays unencrypted in ``.storage``. Only an administrator,
naming one deleted entry and confirming, removes exactly its coordinator,
wallet-ledger and operator-key stores, and only when every present store is
positively mock/testnet. Anything else, or any doubt, is refused untouched.
Key material is never returned or logged.
"""
import json
import logging
import os

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .embedded import _identity
from .records import STORES, RecordVersionError, VersionedStore, check_envelope, check_keys

_LOGGER = logging.getLogger(__name__)
# Key last: an interrupted purge stays recognisable, so a retry completes it.
PURGED = ("coordinator", "wallet_ledger", "operator_key")
NOT_MAINNET = ("embedded-operator-mainnet", "embedded_mainnet", "mainnet")


class Refused(Exception):
    pass


def _read(store):
    try:
        with open(store.path, encoding="utf-8") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None
    except OSError:
        raise Refused(f"{store.record_name} is unreadable") from None
    try:
        check_envelope(store.record_name, raw)
        data = json.loads(raw).get("data")
        check_keys(store.record_name, data)
    except (RecordVersionError, ValueError, AttributeError):
        raise Refused(f"{store.record_name} is not a recognised store") from None
    if not isinstance(data, dict):
        raise Refused(f"{store.record_name} holds no recognisable data")
    return data


def _testnet_key(key):
    if key.get("network") != "testnet" or set(key) != {"secret_hex", "public_key", "address", "network"}:
        return False
    try:
        return _identity(key["secret_hex"], "testnet") == key
    except Exception:  # SDK errors must not leak key material.
        return False


def _testnet_ledger(ledger):
    records = ledger.get("records")
    if set(ledger) - {"records", "last_self_test"} or not isinstance(records, dict):
        return False
    for item in records.values():
        record = item.get("record") if isinstance(item, dict) else None
        payload = item.get("payload") if isinstance(item, dict) else None
        if not (isinstance(record, dict) and isinstance(payload, dict)
                and record.get("network") == "testnet" and record.get("mode") == "embedded_testnet"
                and payload.get("operator_binding_id") == "embedded-operator-testnet"):
            return False
    return True


def _not_mainnet_coordinator(saved, mock_only):
    """mock_only: no wallet stores, so every session must carry the removed mock binding."""
    sessions = saved.get("sessions")
    if not isinstance(sessions, dict):
        return False
    for session in sessions.values():
        if not isinstance(session, dict):
            return False
        if mock_only and session.get("driver_binding_id") != "driver-demo-01":
            return False
        payload, remote = session.get("payload") or {}, session.get("remote") or {}
        if not isinstance(payload, dict) or not isinstance(remote, dict):
            return False
        if {payload.get("operator_binding_id"), remote.get("mode"), remote.get("network")} & set(NOT_MAINNET):
            return False
    return True


def inspect(stores):
    """Names of present purgeable stores; raise Refused on any doubt. Read-only."""
    for name, store in stores.items():
        if name not in PURGED and os.path.lexists(store.path):
            raise Refused(f"a {name} store exists for this ID; it is not a removed testnet/mock entry")
    data = {name: _read(stores[name]) for name in PURGED}
    coordinator, ledger, key = (data[name] for name in PURGED)
    if key is not None and not _testnet_key(key):
        raise Refused("the operator key is not a testnet key")
    if ledger is not None and not (_testnet_ledger(ledger) and (key is not None or ledger["records"])):
        raise Refused("the wallet ledger is not a testnet ledger")
    if coordinator is not None and not _not_mainnet_coordinator(coordinator, key is None and ledger is None):
        raise Refused("the coordinator store may belong to a mainnet wallet")
    return [name for name in PURGED if data[name] is not None]


async def purge(hass, data):
    entry_id = data["entry_id"]
    if data.get("confirm") is not True:
        raise HomeAssistantError("Set confirm: true to remove the stores of a deleted entry")
    if hass.config_entries.async_get_entry(entry_id) is not None or entry_id in hass.data.get(DOMAIN, {}):
        raise HomeAssistantError("A config entry with this ID still exists; delete the entry first")
    stores = {name: VersionedStore(hass, name, entry_id)
              for name, spec in STORES.items() if "{entry_id}" in spec.key}
    try:
        present = await hass.async_add_executor_job(inspect, stores)
    except Refused as exc:
        _LOGGER.warning("Refused to remove stores for entry %s: %s", entry_id, exc)
        raise HomeAssistantError(f"Refused, nothing removed: {exc}") from None
    removed = []
    for name in present:
        await stores[name].async_remove()
        removed.append(stores[name].key)
    if removed:
        _LOGGER.warning("Removed stores of deleted removed-backend entry %s: %s", entry_id, removed)
    return {"entry_id": entry_id, "removed": removed}
