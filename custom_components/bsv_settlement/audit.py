"""Append-only, hash-chained log of mainnet ledger state transitions.

Entries are derived from snapshots *after* they are durably checkpointed, so a
logged transition always describes saved state. The log contains only record
family names, one-way record references and enumerated state names: no keys,
signed bytes, addresses, links, tokens, amounts or user identifiers.

Trust boundary: the log shares the HA storage/backup domain. It detects edits
that break the chain and a ledger restored to a point *before* the logged head
while this log was retained. It cannot detect a coherent rollback of the
ledger, checkpoint and this log together, or a rewrite of the whole chain.
Appending is best-effort and never raises into payment paths: a failed append
is reported and retried with the next save; it never creates or retries a
payment. A broken chain is reported and left untouched, never repaired.
"""
import copy
import hashlib
import json
import logging
import re

from homeassistant.util import dt as dt_util

from .records import VersionedStore

_LOGGER = logging.getLogger(__name__)

SCHEMA = "wallet-audit-v1"
MARKER = "wallet_audit_version"
VERSION = 1
GENESIS = "0" * 64
MAX_ENTRIES = 2000
ENTRY_FIELDS = ("seq", "at", "event", "ns", "ref", "from", "to", "ledger_sequence",
                "ledger_digest", "state_digest", "prev", "hash")
EVENTS = ("baseline", "created", "state", "removed")
STATE = re.compile(r"[a-z][a-z0-9_]{0,63}")
HEX = re.compile(r"[0-9a-f]{64}")
STATEFUL = ("payments", "automatic_credits", "driver_collections", "session_budgets",
            "session_reviews", "closed_sessions", "ongoing_credit_routes",
            "public_registration_windows", "unallocated_receipts")
POLICIES = ("automatic_credit_policy", "ongoing_credit_policy")


class AuditRollback(Exception):
    """The retained audit head is ahead of, or differs from, the loaded ledger."""


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def ref(ns, record_id):
    """One-way reference; an operator can recompute it from a known record ID."""
    return hashlib.sha256(f"bsv-audit-ref-v1\x00{ns}\x00{record_id}".encode()).hexdigest()[:32]


def _state(value):
    return value if isinstance(value, str) and STATE.fullmatch(value) else "unrecognised"


def project(ledger):
    """{family: {ref: state}} for financial/authority records. Pure function."""
    out = {}

    def put(ns, rows):
        if rows:
            out[ns] = {ref(ns, k): v for k, v in rows.items()}

    for ns in STATEFUL:
        rows = ledger.get(ns)
        if isinstance(rows, dict):
            put(ns, {k: _state(r.get("state")) if isinstance(r, dict) else "unrecognised"
                     for k, r in rows.items()})
    records = ledger.get("records")
    if isinstance(records, dict):
        put("records", {k: _state((r.get("record") or {}).get("state"))
                        if isinstance(r, dict) else "unrecognised" for k, r in records.items()})
    monthly = ledger.get("monthly_authorities")
    if isinstance(monthly, dict):
        authorities = monthly.get("authorities")
        if isinstance(authorities, dict):
            put("monthly_authorities.authorities", {
                k: "cancelled" if isinstance(r, dict) and r.get("cancel_proof") else "accepted"
                for k, r in authorities.items()})
        bindings = monthly.get("bindings")
        if isinstance(bindings, dict):
            put("monthly_authorities.bindings", {k: "bound" for k in bindings})
    for ns in POLICIES:
        policy = ledger.get(ns)
        if isinstance(policy, dict):
            put(ns, {"policy": "enabled" if policy.get("enabled") is True else "disabled"})
    if ledger.get("active_payment"):
        put("active_payment", {str(ledger["active_payment"]): "active"})
    recoveries = ledger.get("collection_recoveries")
    if isinstance(recoveries, list):
        put("collection_recoveries", {str(i): "recorded" for i in range(len(recoveries))})
    requests = ledger.get("energy_adjustment_requests")
    if isinstance(requests, dict):
        put("energy_adjustment_requests", {k: "recorded" for k in requests})
    return out


def transitions(old, new):
    """Deterministic ordered (event, ns, ref, from, to) between projections."""
    rows = []
    for ns in sorted(set(old) | set(new)):
        before, after = old.get(ns, {}), new.get(ns, {})
        for key in sorted(set(before) | set(after)):
            a, b = before.get(key), after.get(key)
            if a == b:
                continue
            event = "created" if a is None else "removed" if b is None else "state"
            rows.append((event, ns, key, a, b))
    return rows


def entry_hash(entry):
    return digest({k: entry[k] for k in ENTRY_FIELDS if k != "hash"})


def verify(doc, identity):
    """Raise ValueError unless the document and its whole retained chain verify."""
    if (not isinstance(doc, dict) or set(doc) != {"schema", "identity", "anchor", "baseline", "entries"}
            or doc["schema"] != SCHEMA or doc["identity"] != identity
            or not isinstance(doc["entries"], list) or not doc["entries"]
            or not isinstance(doc["baseline"], dict)):
        raise ValueError("audit document")
    anchor = doc["anchor"]
    if (not isinstance(anchor, dict) or set(anchor) != {"seq", "hash"}
            or type(anchor["seq"]) is not int or anchor["seq"] < 0
            or not isinstance(anchor["hash"], str) or not HEX.fullmatch(anchor["hash"])
            or (anchor["seq"] == 0) != (anchor["hash"] == GENESIS)):
        raise ValueError("audit anchor")
    seq, prev = anchor["seq"], anchor["hash"]
    for entry in doc["entries"]:
        if (not isinstance(entry, dict) or set(entry) != set(ENTRY_FIELDS)
                or type(entry["seq"]) is not int or entry["seq"] != seq + 1
                or entry["prev"] != prev or entry["event"] not in EVENTS
                or type(entry["ledger_sequence"]) is not int or entry["ledger_sequence"] < 1
                or not isinstance(entry["ledger_digest"], str)
                or not HEX.fullmatch(entry["ledger_digest"])
                or not isinstance(entry["hash"], str) or entry_hash(entry) != entry["hash"]):
            raise ValueError(f"audit entry {seq + 1}")
        seq, prev = entry["seq"], entry["hash"]
    if doc["entries"][-1]["state_digest"] != digest(doc["baseline"]):
        raise ValueError("audit baseline")


class AuditLog:
    """Owned by ``CheckpointedStore``; called only while it holds its lock."""

    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.store = VersionedStore(hass, "wallet_audit", entry.entry_id)
        self.state = "not_loaded"   # ready | write_failed | broken | missing
        self.problem = None
        self.doc = None

    def summary(self):
        head = self.doc["entries"][-1] if self.doc else None
        return {"state": self.state, "problem": self.problem,
                "entries_retained": len(self.doc["entries"]) if self.doc else 0,
                "head_seq": head["seq"] if head else None,
                "head_ledger_sequence": head["ledger_sequence"] if head else None}

    def _report(self, state, problem):
        self.state, self.problem = state, problem
        _LOGGER.error("Wallet audit log %s: %s; not repaired, settlement checkpoint unaffected",
                      state, problem)

    async def async_load(self, ledger, sequence, ledger_digest):
        """Verify the retained chain; raise ``AuditRollback`` for a stale ledger."""
        identity = self.entry.data.get("operator_public_key")
        marker = self.entry.data.get(MARKER)
        try:
            saved = await self.store.async_load()
        except Exception as exc:
            # Corrupt/unknown/newer versions are refused in place, never reset.
            return self._report("broken", f"unreadable ({type(exc).__name__})")
        if saved is None:
            if marker is not None:
                return self._report("missing", "established audit log is absent")
            self.doc = {"schema": SCHEMA, "identity": identity,
                        "anchor": {"seq": 0, "hash": GENESIS}, "baseline": {}, "entries": []}
            self.state = "ready"
            await self._append([("baseline", "*", "", None, None)], project(ledger),
                               sequence, ledger_digest)
            if self.state == "ready":
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**self.entry.data, MARKER: VERSION})
            return None
        try:
            verify(saved, identity)
        except ValueError as exc:
            return self._report("broken", f"chain verification failed at {exc}")
        head = saved["entries"][-1]
        # The head is written only after its ledger revision is durable, so it
        # can lag the ledger but never lead it or name a different revision.
        if head["ledger_sequence"] > sequence or (
                head["ledger_sequence"] == sequence and head["ledger_digest"] != ledger_digest):
            raise AuditRollback()
        self.doc, self.state = saved, "ready"
        if marker is None:
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, MARKER: VERSION})
        # Transitions saved while appending failed, or by a release without this
        # log, are recorded now rather than lost. No-op when nothing changed.
        await self._append([], project(ledger), sequence, ledger_digest)
        return None

    async def record(self, snapshot, sequence, ledger_digest):
        """Append transitions for a durably saved snapshot. Never raises Exception."""
        if self.state not in ("ready", "write_failed"):
            return
        try:
            await self._append([], project(snapshot), sequence, ledger_digest)
        except Exception:
            self._report("write_failed", "append failed")

    async def _append(self, extra, current, sequence, ledger_digest):
        rows = extra + transitions(self.doc["baseline"], current)
        if not rows:
            return
        doc = copy.deepcopy(self.doc)
        doc["baseline"] = current
        state_digest = digest(current)
        at = dt_util.utcnow().isoformat(timespec="seconds")
        for event, ns, key, before, after in rows:
            last = doc["entries"][-1] if doc["entries"] else doc["anchor"]
            entry = {"seq": last["seq"] + 1, "at": at, "event": event, "ns": ns, "ref": key,
                     "from": before, "to": after, "ledger_sequence": sequence,
                     "ledger_digest": ledger_digest, "state_digest": state_digest,
                     "prev": last["hash"]}
            entry["hash"] = entry_hash(entry)
            doc["entries"].append(entry)
        if len(doc["entries"]) > MAX_ENTRIES:
            # Bounded retention: the anchor keeps the trimmed prefix's head hash.
            dropped = doc["entries"][:-MAX_ENTRIES]
            doc["entries"] = doc["entries"][-MAX_ENTRIES:]
            doc["anchor"] = {"seq": dropped[-1]["seq"], "hash": dropped[-1]["hash"]}
        try:
            await self.store.async_save(doc)
        except Exception:
            # The ledger is already durable; keep the old baseline so the next
            # save records these transitions. Never surface into payment paths.
            return self._report("write_failed", "append could not be saved")
        self.doc, self.state, self.problem = doc, "ready", None
