"""Registry of every persisted record family and its fail-closed load policy.

Trust boundary: files under HA ``.storage`` are local, operator-controlled and
restored together from backups. This module refuses data it cannot interpret
(corrupt JSON, unknown/newer version, unknown ledger namespace)
instead of resetting it. It never rewrites, renames or deletes a refused file.
It does not prove freshness; a coherent full-backup rollback still passes.
"""
from dataclasses import dataclass
import json
import os

from homeassistant.helpers.storage import Store

from .const import DOMAIN


class RecordVersionError(ValueError):
    """Persisted record is corrupt, foreign or of an unsupported version."""


@dataclass(frozen=True)
class StoreSpec:
    key: str                  # HA storage key; "{entry_id}" is substituted
    version: int              # HA envelope major version written by this release
    minor_version: int        # HA envelope minor version written by this release
    owner: str                # module that loads and writes the store
    migrations: tuple = ()    # older major versions with an explicit, tested converter
    keys: frozenset = None    # exact permitted top-level keys, when owner-validated here
    inner: str = None         # in-data schema marker, where one exists
    private: bool = False
    atomic: bool = False
    secret: bool = False      # contains key material; never exported or audited
    note: str = ""


LEDGER_NAMESPACES = {
    # name: (primary owner module, in-data schema marker or None)
    "records": ("embedded", None),
    "last_self_test": ("embedded", None),
    "wallet_checkpoint_version": ("ledger_checkpoint", "int 1"),
    "driver": ("mainnet", None),
    "payments": ("mainnet", None),
    "active_payment": ("mainnet", None),
    "chain": ("mainnet", None),
    "session_reviews": ("session_review", None),
    "session_review_index": ("session_review", None),
    "latest_session_review": ("session_review", None),
    "received_outpoints": ("session_review", None),
    "session_budgets": ("budget", None),
    "latest_session_budget": ("budget", None),
    "public_registration_windows": ("enrolment", None),
    "driver_collections": ("collection", None),
    "driver_collection_index": ("collection", None),
    "collection_recoveries": ("collection_recovery", None),
    "driver_confirmation_schedule": ("confirmation_scheduler", None),
    "automatic_credit_policy": ("auto_credit", None),
    "automatic_credits": ("auto_credit", None),
    "automatic_credit_index": ("auto_credit", None),
    "ongoing_credit_policy": ("ongoing_credit", None),
    "ongoing_credit_routes": ("ongoing_credit", None),
    "messagebox_delivery_policy": ("messagebox", None),
    "closed_sessions": ("session_closure", None),
    "unallocated_receipts": ("owned_waiver", None),
    "energy_adjustment_requests": ("energy_adjustment", None),
    "monthly_authorities": ("monthly_authority", "schema monthly-authorities-v1"),
}

STORES = {
    "coordinator": StoreSpec(
        f"{DOMAIN}.{{entry_id}}", 1, 1, "coordinator", keys=frozenset({"sessions", "latest"}),
        note="Mainnet session bindings and frozen draft payloads. Removed mock/testnet entries "
             "left files of the same key families (with operator_key, wallet_ledger); kept registered "
             "so they stay recognised, and never rewritten."),
    "operator_key": StoreSpec(
        f"{DOMAIN}.operator_key.{{entry_id}}", 1, 1, "embedded", private=True, atomic=True,
        secret=True, note="Operator key; identity re-derived and compared on load."),
    "wallet_ledger": StoreSpec(
        f"{DOMAIN}.embedded.{{entry_id}}", 1, 1, "embedded", keys=frozenset(LEDGER_NAMESPACES),
        inner="wallet_checkpoint_version (mainnet)", private=True, atomic=True,
        note="Authoritative payment/authority ledger; namespaces listed in LEDGER_NAMESPACES."),
    "ledger_checkpoint": StoreSpec(
        f"{DOMAIN}.ledger_checkpoint.{{entry_id}}", 1, 1, "ledger_checkpoint",
        keys=frozenset({"version", "sequence", "identity", "digest"}), inner="version 1",
        private=True, atomic=True, note="Witness digest/sequence for the mainnet ledger."),
    "wallet_audit": StoreSpec(
        f"{DOMAIN}.audit.{{entry_id}}", 1, 1, "audit",
        keys=frozenset({"schema", "identity", "anchor", "baseline", "entries"}),
        inner="schema wallet-audit-v1", private=True, atomic=True,
        note="Hash-chained transition log for the mainnet ledger."),
    "proxy": StoreSpec(
        f"{DOMAIN}.proxy.{{entry_id}}", 1, 1, "proxy",
        keys=frozenset({"observations", "archive", "persistent_issues", "checkpoint_at",
                        "tariff_provenance"}),
        note="Sensor observations, proxy session archive and append-only tariff provenance."),
    "provenance_archive": StoreSpec(
        f"{DOMAIN}.provenance_archive.{{entry_id}}", 1, 1, "provenance_archive",
        keys=frozenset({"schema", "versions"}), inner="schema bsv_settlement.provenance_archive.v1",
        private=True, atomic=True,
        note="Full frozen tariff provenance versions referenced by wallet-ledger records."),
    "ocpp_shadow": StoreSpec(
        f"{DOMAIN}.ocpp_shadow.{{entry_id}}", 3, 1, "ocpp_shadow", migrations=(1, 2),
        inner="schema 3", note="Shadow-only OCPP import/export ledger; 1 and 2 convert."),
    "ocpp_lifecycle": StoreSpec(
        f"{DOMAIN}.ocpp_lifecycle.{{entry_id}}", 1, 1, "ocpp_shadow",
        keys=frozenset({"schema", "recorded_since", "saved_at", "tracker", "spans",
                        "spans_trimmed", "late_final"}),
        inner="schema 1", note="Shadow-only OCPP native session lifecycle; one-way token references only."),
    "recorder_reconciliation": StoreSpec(
        f"{DOMAIN}.recorder_reconciliation.{{entry_id}}", 1, 1, "recorder", inner="schema 1",
        note="Read-only legacy recorder reconciliation results."),
    "grouped_wallet_test": StoreSpec(
        f"{DOMAIN}_grouped_wallet_test", 1, 1, "grouped_wallet_test",
        keys=frozenset({"enabled", "origin"}), note="Global diagnostic manifest setting."),
}


def storage_key(name, entry_id=None):
    spec = STORES[name]
    if ("{entry_id}" in spec.key) != (entry_id is not None):
        raise KeyError(name)
    return spec.key.format(entry_id=entry_id)


def spec_for_key(key):
    """(name, spec) for an on-disk storage key, or None if not ours/unknown."""
    for name, spec in STORES.items():
        prefix, _, suffix = spec.key.partition("{entry_id}")
        if "{entry_id}" not in spec.key:
            if key == spec.key:
                return name, spec
        elif key.startswith(prefix) and len(key) > len(prefix) + len(suffix) and "." not in key[len(prefix):]:
            return name, spec
    return None


def check_envelope(name, raw):
    """Validate a raw HA storage envelope without trusting or changing it."""
    spec = STORES[name]
    try:
        envelope = json.loads(raw)
    except ValueError:
        raise RecordVersionError(f"{name}: corrupt storage file") from None
    if envelope == {}:
        return  # HA treats an empty object as no data; owners apply missing-store rules.
    # The envelope key is not compared: HA never reads it, and fixtures/restores
    # may copy files between entries. Owners bind identity (e.g. checkpoint).
    if not isinstance(envelope, dict) or "data" not in envelope:
        raise RecordVersionError(f"{name}: malformed storage file")
    major, minor = envelope.get("version"), envelope.get("minor_version", 1)
    if type(major) is not int or type(minor) is not int:
        raise RecordVersionError(f"{name}: invalid storage version")
    if (major, minor) != (spec.version, spec.minor_version) and major not in spec.migrations:
        raise RecordVersionError(
            f"{name}: unsupported storage version {major}.{minor}; this release reads "
            f"{spec.version}.{spec.minor_version}")


def check_keys(name, data):
    """Refuse unknown top-level keys, e.g. data written by a newer release."""
    spec = STORES[name]
    if data is None or spec.keys is None:
        return
    if not isinstance(data, dict):
        raise RecordVersionError(f"{name}: invalid record")
    unknown = set(data) - spec.keys
    if unknown:
        raise RecordVersionError(f"{name}: unknown record namespaces {sorted(unknown)}")


class VersionedStore(Store):
    """HA Store bound to one registry entry.

    Before HA parses a file, the raw envelope is checked so that corrupt JSON is
    refused in place (HA would otherwise rename it and return an empty store),
    and so that a newer minor version is refused rather than silently rewritten
    at this release's version. Subclasses supply explicit migrations only.
    """

    def __init__(self, hass, name, entry_id=None):
        spec = STORES[name]
        super().__init__(hass, spec.version, storage_key(name, entry_id),
                         minor_version=spec.minor_version, private=spec.private,
                         atomic_writes=spec.atomic)
        self.record_name = name

    async def async_load(self):
        if self._data is None:  # Nothing pending in memory; check the file itself.
            await self.hass.async_add_executor_job(self._check_file)
        return await super().async_load()

    def _check_file(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = handle.read()
        except FileNotFoundError:
            return
        except OSError:
            raise RecordVersionError(f"{self.record_name}: unreadable storage file") from None
        check_envelope(self.record_name, raw)

    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        # No converter is registered for this store: never pass data through.
        raise RecordVersionError(f"{self.record_name}: no migration from "
                                 f"{old_major_version}.{old_minor_version}")


def inspect_directory(path):
    """Read-only report for an HA ``.storage`` directory (restore drills)."""
    rows = []
    for filename in sorted(os.listdir(path)):
        if not filename.startswith(DOMAIN):
            continue
        full = os.path.join(path, filename)
        if not os.path.isfile(full):
            continue
        match = spec_for_key(filename)
        if match is None:
            rows.append({"file": filename, "record": None, "result": "unregistered"})
            continue
        name, _ = match
        try:
            with open(full, encoding="utf-8") as handle:
                raw = handle.read()
            check_envelope(name, raw)
            check_keys(name, (json.loads(raw) or {}).get("data"))
            result = "ok"
        except (OSError, RecordVersionError) as exc:
            result = f"refused: {exc}"
        rows.append({"file": filename, "record": name, "result": result})
    return rows
