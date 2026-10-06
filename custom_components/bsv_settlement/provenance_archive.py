"""Frozen tariff provenance: a fixed-size ledger reference plus a separate archive.

Wallet-ledger records (manual reviews, collections, operator credits) keep only
``tariff_provenance_ref``: a small, fixed-shape reference outside any signed or
hashed terms. The full version lives in this entry's private, append-only
``provenance_archive`` store, keyed by ``version_digest`` (SHA-256 of the
canonical version JSON), so identical versions are stored once. The archive is
written before the ledger record that references it is saved.

Evidence only. Nothing here raises into, delays or changes a payment: a missing
recorder, lookup fault or archive failure yields ``not_recorded`` or
``archive_missing``. A refused archive file is never rewritten or reset.
"""
import copy
import logging

from .records import VersionedStore, check_keys
from .tariff_provenance import DIRECTIONS, NOT_RECORDED, digest, recorded, summary, verify

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
SCHEMA = "bsv_settlement.provenance_archive.v1"
MAX_UNREFERENCED = 50      # unreferenced entries (e.g. a ledger save that failed) kept, oldest dropped first
REFERRERS = ("session_reviews", "driver_collections", "automatic_credits")
REF = "tariff_provenance_ref"
NOT_RECORDED_REF = {"status": "not_recorded"}
ARCHIVE_MISSING = ("The ledger references a frozen tariff provenance version that is not in "
                   "this entry's provenance archive. Nothing is inferred.")


def reference(version):
    """Fixed-shape reference to one captured version: no intervals or source rows."""
    body = version["provenance"]
    return {
        "status": "recorded", "schema": body["schema"], "version": version["version"],
        "captured_at": version["captured_at"], "account_digest": version["account_digest"],
        "provenance_digest": version["provenance_digest"], "version_digest": digest(version),
        "directions": {d: {
            "interval_count": int(body["directions"][d]["interval_count"]),
            "estimate_interval_count": int(body["directions"][d]["estimate_interval_count"]),
            "truncated": bool(body["directions"][d]["truncated"])} for d in DIRECTIONS},
        "estimated": any(body["directions"][d]["estimate_interval_count"] for d in DIRECTIONS),
    }


def freeze(api, proxy_entry, session_id, account_digest):
    """(reference, version|None) for exactly this frozen account. Never raises."""
    try:
        from .const import DOMAIN
        proxy = api.hass.data.get(DOMAIN, {}).get(proxy_entry)
        version = proxy.tariff_provenance(session_id, account_digest, detail=True)
        if recorded(version) and version.get("account_digest") == account_digest:
            return reference(version), copy.deepcopy(version)
    except Exception:  # noqa: BLE001 - missing recorder or lookup fault: record the gap
        pass
    return copy.deepcopy(NOT_RECORDED_REF), None


async def archive(api, version):
    """Write the full version before the referencing ledger save. Never raises."""
    if version is None:
        return False
    try:
        store = getattr(api, "provenance_archive", None)
        return bool(store) and await store.put(api.saved, version)
    except Exception:  # noqa: BLE001 - evidence must never block a payment
        _LOGGER.exception("Tariff provenance archive write failed; payment is unaffected")
        return False


def view(api, record, detail=False):
    """Read-only operator view. Compact: the reference only. Detail: resolved from the archive."""
    legacy = record.get("tariff_provenance")
    if recorded(legacy):  # Reviews frozen before the archive held the full version inline.
        if detail:
            return copy.deepcopy(legacy) | {"status": "recorded", "digest_verified": verify(legacy)}
        return summary(legacy) | {"digest_verified": verify(legacy)}
    ref = record.get(REF)
    if not isinstance(ref, dict) or ref.get("status") != "recorded":
        return copy.deepcopy(NOT_RECORDED)
    if not detail:
        return copy.deepcopy(ref)
    store = getattr(api, "provenance_archive", None)
    version = store.get(ref.get("version_digest")) if store else None
    if version is None:
        return copy.deepcopy(ref) | {"status": "archive_missing", "reason": ARCHIVE_MISSING}
    verified = (verify(version) and digest(version) == ref["version_digest"]
                and version.get("account_digest") == ref["account_digest"])
    return version | {"status": "recorded", "digest_verified": verified, "reference": copy.deepcopy(ref)}


def referenced(ledger):
    """Version digests referenced by any wallet-ledger record."""
    found = set()
    for namespace in REFERRERS:
        rows = ledger.get(namespace)
        for row in (rows.values() if isinstance(rows, dict) else ()):
            ref = row.get(REF) if isinstance(row, dict) else None
            if isinstance(ref, dict) and isinstance(ref.get("version_digest"), str):
                found.add(ref["version_digest"])
    return found


class ProvenanceArchive:
    """Append-only {version_digest: {archived_at, version}}. Refused files are never rewritten."""

    def __init__(self, hass, entry_id):
        self.store = VersionedStore(hass, "provenance_archive", entry_id)
        self.data = {"schema": SCHEMA, "versions": {}}
        self.state = "not_loaded"

    async def load(self):
        try:
            saved = await self.store.async_load()
            check_keys("provenance_archive", saved)
            if saved is not None:
                if saved.get("schema") != SCHEMA or not isinstance(saved.get("versions"), dict):
                    raise ValueError("unrecognised provenance archive")
                self.data = saved
            self.state = "ok"
        except Exception:  # noqa: BLE001 - evidence store; the wallet still loads
            _LOGGER.warning("Tariff provenance archive refused; it is left unchanged and not written")
            self.state = "refused"

    def get(self, version_digest):
        entry = self.data["versions"].get(version_digest) if self.state == "ok" else None
        return copy.deepcopy(entry["version"]) if isinstance(entry, dict) else None

    async def put(self, ledger, version):
        if self.state != "ok" or not verify(version):
            return False
        key = digest(version)
        versions = self.data["versions"]
        if key in versions:
            return True
        from homeassistant.util import dt as dt_util
        versions[key] = {"archived_at": dt_util.utcnow().isoformat(), "version": copy.deepcopy(version)}
        keep = referenced(ledger) | {key}
        loose = sorted((k for k in versions if k not in keep), key=lambda k: versions[k]["archived_at"])
        dropped = {k: versions.pop(k) for k in loose[:max(0, len(loose) - MAX_UNREFERENCED)]}
        try:
            await self.store.async_save({"schema": SCHEMA, "versions": dict(versions)})
        except BaseException:
            versions.pop(key, None)
            versions.update(dropped)
            raise
        return True

    def summary(self):
        return {"state": self.state, "versions": len(self.data["versions"])}
