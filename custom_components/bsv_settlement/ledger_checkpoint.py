"""Detect split/missing local restores, not rollback of an entire HA backup."""
import asyncio
import copy
import hashlib
import json
import logging


from .api import WalletError
from .audit import AuditLog, AuditRollback
from .records import VersionedStore

VERSION = 1
MARKER = "wallet_checkpoint_version"
ERROR = "Wallet ledger checkpoint mismatch; stop settlement and reconcile the matching backup"
_LOGGER = logging.getLogger(__name__)


def fingerprint(data):
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class CheckpointedStore:
    """Write witness first; any interrupted split write must fail closed.

    This witness shares the HA trust/backup domain. It is not an independent
    monotonic counter or proof that a coherently restored backup is current.
    """

    def __init__(self, hass, entry, ledger):
        self.hass = hass
        self.entry = entry
        self.ledger = ledger
        self.witness = VersionedStore(hass, "ledger_checkpoint", entry.entry_id)
        self.audit = AuditLog(hass, entry)
        self.existing_identity = bool(entry.data.get("operator_public_key"))
        self.lock = asyncio.Lock()
        self.blocked = True
        self.sequence = 0
        self.last_digest = None

    async def async_load(self):
        async with self.lock:
            self.blocked = True
            try:
                data = await self.ledger.async_load()
                witness = await self.witness.async_load()
                marker = self.entry.data.get(MARKER)
                if marker is not None and (type(marker) is not int or marker != VERSION):
                    raise ValueError()
                if data is None:
                    if self.existing_identity or witness is not None or marker is not None:
                        raise ValueError()
                    data = {"records": {}, "last_self_test": None}
                if not isinstance(data, dict) or not isinstance(data.get("records"), dict):
                    raise ValueError()
                if "last_self_test" not in data:
                    raise ValueError()
                data_marker = data.get(MARKER)
                if data_marker is not None and (
                        type(data_marker) is not int or data_marker != VERSION):
                    raise ValueError()
                if witness is None:
                    if marker is not None or data_marker is not None:
                        raise ValueError()
                    # First adoption records a baseline; it cannot attest that
                    # the pre-checkpoint legacy ledger was already up to date.
                    data = copy.deepcopy(data)
                    data[MARKER] = VERSION
                    self.blocked = False
                    await self._save(data)
                else:
                    if (not isinstance(witness, dict) or
                            type(witness.get("version")) is not int or
                            witness["version"] != VERSION or
                            type(witness.get("sequence")) is not int or
                            witness["sequence"] < 1 or data_marker != VERSION or
                            witness.get("identity") != self.entry.data.get("operator_public_key") or
                            witness.get("digest") != fingerprint(data)):
                        raise ValueError()
                    self.sequence = witness["sequence"]
                    self.last_digest = witness["digest"]
                if marker is None:
                    self.hass.config_entries.async_update_entry(
                        self.entry, data={**self.entry.data, MARKER: VERSION})
                # A retained audit head beyond this ledger means a stale restore.
                await self.audit.async_load(data, self.sequence, self.last_digest)
                self.blocked = False
                return data
            except asyncio.CancelledError:
                self.blocked = True
                raise
            except AuditRollback:
                self.blocked = True
                _LOGGER.error("Wallet ledger is older than its retained audit log; stale restore")
                raise WalletError(ERROR) from None
            except Exception:
                self.blocked = True
                raise WalletError(ERROR) from None

    async def async_save(self, data):
        async with self.lock:
            await self._save(data)

    async def _save(self, data):
        if self.blocked:
            raise WalletError(ERROR)
        # Copy before the first await; never hash one version and save another.
        try:
            snapshot = copy.deepcopy(data)
            if (not isinstance(snapshot, dict) or
                    type(snapshot.get(MARKER)) is not int or snapshot[MARKER] != VERSION):
                raise ValueError()
            digest = fingerprint(snapshot)
        except Exception:
            self.blocked = True
            raise WalletError(ERROR) from None
        if digest == self.last_digest:
            return
        self.blocked = True
        witness = {"version": VERSION, "sequence": self.sequence + 1,
                   "identity": self.entry.data["operator_public_key"], "digest": digest}
        try:
            await self.witness.async_save(witness)
            await self.ledger.async_save(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise WalletError(ERROR) from None
        self.sequence = witness["sequence"]
        self.last_digest = digest
        self.blocked = False
        # After the durable write; reports failures itself and never raises them.
        await self.audit.record(snapshot, self.sequence, digest)
