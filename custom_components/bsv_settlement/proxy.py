"""Persistent read-only HA recorder, isolated from wallet and charger actions."""
import copy
from datetime import datetime, timedelta
from functools import partial
import logging
import time

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .records import VersionedStore, check_keys
from .proxy_ledger import build_records, instant
from .tariff_provenance import TariffProvenanceLedger, capture, summary

_LOGGER = logging.getLogger(__name__)
SOURCE_KEYS = ("import", "export", "state", "import_price", "export_price")
MAX_ROWS = 60000
ARCHIVE_LIMIT = 50   # Newest summaries kept regardless of references.
MAX_PINNED = 200     # Older summaries kept because an open wallet record needs them.
PIN_MARGIN = 5       # Warn this many places before a pinned summary would be dropped.
PERSISTENT_ISSUES = frozenset({
    "observation_limit_reached", "restart_gap_exceeds_24_hour_backfill",
    "pinned_session_limit_exceeded"})
# Issues an administrator may acknowledge (#108). Windowed ones keep flagging
# every session that overlapped the affected time, so its account stays blocked.
WINDOWED_ISSUES = ("observation_limit_reached", "restart_gap_exceeds_24_hour_backfill")
ACKNOWLEDGEABLE_ISSUES = (*WINDOWED_ISSUES, "pinned_session_limit_exceeded")
MAX_WINDOWS = 20


def normalize(state):
    attrs = state.attributes
    return {
        "t": dt_util.as_local(state.last_updated).isoformat(), "value": state.state,
        **({"unit": attrs["unit_of_measurement"]} if "unit_of_measurement" in attrs else {}),
        **{key: attrs[field].isoformat() if isinstance(attrs[field], datetime) else attrs[field]
           for key, field in (
            ("start", "start_time"), ("end", "end_time"), ("estimate", "estimate"))
           if field in attrs},
    }


def registry_identity(hass, entity_id):
    """Entity-registry identity of one source; ``unregistered`` if it has none."""
    row = er.async_get(hass).async_get(entity_id)
    if row is None:
        return {"entity_id": entity_id, "unregistered": True}
    return {"entity_id": entity_id, "platform": row.platform, "unique_id": row.unique_id,
            "config_entry_id": row.config_entry_id}


def pin_sources(hass, sources):
    return {key: registry_identity(hass, entity) for key, entity in sources.items()}


def retain(summaries, pinned, limit=None, max_pinned=None):
    """Newest ``limit`` plus up to ``max_pinned`` older pinned, in order.

    Returns (kept, dropped pinned summaries); the newest pinned stay.
    """
    limit = ARCHIVE_LIMIT if limit is None else limit
    max_pinned = MAX_PINNED if max_pinned is None else max_pinned
    older = [r for r in summaries[:-limit] if r["session_id"] in pinned]
    dropped = older[:-max_pinned] if len(older) > max_pinned else []
    keep = {id(r) for r in (*older[len(dropped):], *summaries[-limit:])}
    return [r for r in summaries if id(r) in keep], dropped


def merge_rows(existing, incoming):
    merged = {r["t"]: r for r in existing}
    for row in incoming:
        merged[row["t"]] = row
    return sorted(merged.values(), key=lambda row: instant(row["t"]))


class ProxyCoordinator(DataUpdateCoordinator):
    mode = "sensor_proxy"

    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name="BSV charging sessions", config_entry=entry,
                         update_interval=timedelta(seconds=15))
        self.entry = entry
        self.sources = {k: entry.data[k + "_entity"] for k in SOURCE_KEYS}
        self.store = VersionedStore(hass, "proxy", entry.entry_id)
        self.observations = {key: [] for key in SOURCE_KEYS}
        self.archive = []
        self.issues = set()
        self.cancel_listener = None
        self.last_save = 0
        self.last_latest_id = None
        self.provenance = TariffProvenanceLedger()
        # Pinned at creation, or adopted once on first load (#107).
        self.pinned = entry.data.get("source_identity")
        # Acknowledged issue windows (config entry data); open ones are in memory.
        self.windows = list(entry.data.get("recorder_issue_windows", []))
        self.issue_window = {}

    def _update_entry(self, **changes):
        """Persist to config entry data; in-memory only for an unregistered entry."""
        entries = getattr(self.hass, "config_entries", None)
        if entries is not None and entries.async_get_entry(self.entry.entry_id) is self.entry:
            entries.async_update_entry(self.entry, data={**self.entry.data, **changes})

    def adopt_identity(self):
        if self.pinned is not None:
            return
        self.pinned = pin_sources(self.hass, self.sources)
        adopted_at = dt_util.utcnow().isoformat()
        _LOGGER.warning("Proxy %s adopted its current source registry identities once",
                        self.entry.entry_id)
        self._update_entry(source_identity=self.pinned, source_binding_adopted_at=adopted_at)

    def binding(self, key):
        """pinned | unregistered | missing | changed | unpinned (never loaded)."""
        if self.pinned is None:
            return "unpinned"
        entity = self.sources[key]
        current = registry_identity(self.hass, entity)
        pinned = self.pinned.get(key) if isinstance(self.pinned, dict) else None
        if current.get("unregistered") and self.hass.states.get(entity) is None:
            return "missing"  # Renamed or removed: the unavailable issue applies.
        if pinned != current:
            return "changed"
        return "unregistered" if current.get("unregistered") else "pinned"

    async def load(self):
        saved = await self.store.async_load()
        check_keys("proxy", saved)
        if saved:
            self.observations = saved["observations"]
            self.archive = saved.get("archive", [])
            self.issues.update(saved.get("persistent_issues", []))
            # Restored from a previous run: when the loss began is unknown.
            for issue in WINDOWED_ISSUES:
                if issue in self.issues:
                    self.issue_window[issue] = (None, None)
            # Older stores have no provenance: it stays "not recorded", never invented.
            self.provenance = TariffProvenanceLedger(saved.get("tariff_provenance"))
        self.adopt_identity()
        end = dt_util.utcnow()
        start = end - timedelta(hours=24)
        gap = None
        if saved and saved.get("checkpoint_at"):
            start = max(start, dt_util.parse_datetime(saved["checkpoint_at"]) - timedelta(minutes=5))
            if dt_util.parse_datetime(saved["checkpoint_at"]) < end - timedelta(hours=24):
                self.issues.add("restart_gap_exceeds_24_hour_backfill")
                gap = saved["checkpoint_at"]
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import get_significant_states
            # Attribute-only updates carry tariff effective periods and finality.
            history = await get_instance(self.hass).async_add_executor_job(partial(
                get_significant_states, self.hass, start, end,
                entity_ids=list(self.sources.values()), significant_changes_only=False,
                minimal_response=False, no_attributes=False))
            for key, entity in self.sources.items():
                if self.binding(key) == "changed":
                    continue  # Never backfill another sensor's history.
                rows = [normalize(state) for state in history.get(entity, [])]
                if not rows:
                    self.issues.add(key + ":history_unavailable")
                self.observations[key] = merge_rows(self.observations[key], rows)
        except Exception:
            _LOGGER.warning("Proxy history backfill unavailable; observation remains provisional")
            self.issues.add("history_backfill_failed")
        if gap:
            # Lost: checkpoint to the backfill start, or to now if backfill fell short.
            short = any(i == "history_backfill_failed" or i.endswith(":history_unavailable")
                        for i in self.issues)
            restored = self.issue_window.get("restart_gap_exceeds_24_hour_backfill")
            self.issue_window["restart_gap_exceeds_24_hour_backfill"] = (
                None if restored else gap, (end if short else start).isoformat())
        # From this point there is no await before listener registration and snapshot.
        self.cancel_listener = async_track_state_change_event(
            self.hass, list(self.sources.values()), self._state_changed)
        self.snapshot_current()

    def referenced_sessions(self):
        """(IDs open wallet records still need, complete?) via a small duck API.

        Each loaded mainnet coordinator answers ``unresolved_proxy_sessions``.
        An enabled mainnet entry that is not loaded, or a provider fault, makes
        the answer incomplete; the caller then evicts nothing already archived.
        """
        pins, complete, answered = set(), True, set()
        for entry_id, coordinator in list(self.hass.data.get(DOMAIN, {}).items()):
            provider = getattr(coordinator, "unresolved_proxy_sessions", None)
            if getattr(coordinator, "mode", None) != "embedded_mainnet" or provider is None:
                continue
            try:
                pins |= {sid for sid in provider(self.entry.entry_id) if isinstance(sid, str)}
                answered.add(entry_id)
            except Exception:  # noqa: BLE001 - unknown pins never evict
                _LOGGER.exception("Wallet session references unavailable; archive eviction paused")
                complete = False
        entries = getattr(self.hass, "config_entries", None)
        if entries is not None:
            for entry in entries.async_entries(DOMAIN):
                if (entry.data.get("backend") == "embedded_mainnet" and entry.disabled_by is None
                        and entry.entry_id not in answered):
                    complete = False
        return pins, complete

    def _retain_archive(self, records, pins, complete):
        known = {r["session_id"]: r for r in self.archive}
        known.update({r["session_id"]: r for r in records})
        held = pins if complete else pins | {r["session_id"] for r in self.archive}
        self.archive, dropped = retain(list(known.values()), held)
        if dropped:
            # Never silent: blocks until an administrator acknowledges it.
            _LOGGER.warning("Proxy archive dropped %d referenced session summaries beyond %d pinned",
                            len(dropped), MAX_PINNED)
            self.issues.add("pinned_session_limit_exceeded")

    @callback
    def _state_changed(self, event):
        state = event.data.get("new_state")
        if state:
            for key, entity in self.sources.items():
                if entity == state.entity_id and self.binding(key) != "changed":
                    self._append(key, normalize(state))

    @callback
    def _append(self, key, row):
        rows = self.observations[key]
        if rows and row == rows[-1]:
            return
        if len(rows) >= MAX_ROWS:
            self.issues.add("observation_limit_reached")
            # From the first lost row (None: unknown, restored after a restart),
            # open again until a prune makes room.
            since = row["t"]
            if "observation_limit_reached" in self.issue_window:
                since = self.issue_window["observation_limit_reached"][0]
                if since is not None and instant(row["t"]) < instant(since):
                    since = row["t"]
            self.issue_window["observation_limit_reached"] = (since, None)
            return
        # Events are normally ordered, but attribute-only updates can share timestamps.
        if rows and instant(row["t"]) <= instant(rows[-1]["t"]):
            self.observations[key] = merge_rows(rows, [row])
        else:
            rows.append(row)

    @callback
    def snapshot_current(self):
        for key, entity in self.sources.items():
            state = self.hass.states.get(entity)
            if state and self.binding(key) != "changed":
                self._append(key, normalize(state))

    async def _async_update_data(self):
        self.snapshot_current()
        as_of = dt_util.now().isoformat()
        observations = copy.deepcopy(self.observations)
        records = await self.hass.async_add_executor_job(
            build_records, observations, self.sources["state"], as_of)
        records = [self._flag(r) for r in records]
        self.archive = [self._flag(r) for r in self.archive]
        unavailable = [
            key for key, entity in self.sources.items()
            if (state := self.hass.states.get(entity)) is None
            or state.state in ("unknown", "unavailable")
        ]
        pins, complete = self.referenced_sessions()
        # Keep latest ended session plus current/most recent session for repricing.
        # Keep the preceding baseline for each source and a bounded summary archive.
        if len(records) > 2:
            cut = instant(records[-2]["opened_at"])
            self._retain_archive(records[:-2], pins, complete)
            for key, rows in self.observations.items():
                before = [r for r in rows if instant(r["t"]) < cut]
                after = [r for r in rows if instant(r["t"]) >= cut]
                self.observations[key] = before[-1:] + after
        window = self.issue_window.get("observation_limit_reached")
        if (window and window[1] is None
                and all(len(rows) < MAX_ROWS for rows in self.observations.values())):
            self.issue_window["observation_limit_reached"] = (window[0], as_of)
        latest = records[-1] if records else None
        previous = records[-2] if len(records) > 1 else (self.archive[-1] if self.archive else None)
        bindings = {key: self.binding(key) for key in self.sources}
        # Live, not stored: persists while the registry differs from the pin.
        changed = {"source_binding_changed"} if "changed" in bindings.values() else set()
        issues = sorted(self.issues | changed | {key + ":unavailable" for key in unavailable})
        retention = self._retention_warnings(records, pins, complete)
        if issues and latest:
            latest = {**latest, "net_cost_aud": None, "net_cost_aud_unrounded": None,
                      "quality_flags": sorted(set(latest["quality_flags"]) | set(issues))}
        provenance_changed = await self._capture_provenance(
            observations, [*records[:-1], latest] if latest else records)
        current_id = latest["session_id"] if latest else None
        if (provenance_changed or current_id != self.last_latest_id
                or time.monotonic() - self.last_save >= 300):
            await self.persist()
            self.last_latest_id = current_id
        return {
            "state": "degraded" if issues else "recording" if latest and not latest["ended_at"] else "waiting",
            "updated_at": as_of, "latest_session": latest, "previous_session": previous,
            "issues": issues, "source_entities": self.sources, "billing_eligible": False,
            "warnings": retention["warnings"] + [
                key + ":source_unregistered" for key, state in bindings.items() if state == "unregistered"],
            "archive_retention": retention["retention"], "source_identity": bindings,
            "source_binding_adopted_at": self.entry.data.get("source_binding_adopted_at"),
            "acknowledged_issues": [{k: w.get(k) for k in ("issue", "since", "until", "acknowledged_at")}
                                    for w in self.windows if isinstance(w, dict)],
        }

    def _flag(self, record):
        """Add each acknowledged issue whose window the session overlapped."""
        flags = set()
        for window in self.windows:
            try:
                opened, ended = instant(record["opened_at"]), record.get("ended_at")
                if opened <= instant(window["until"]) and (
                        window.get("since") is None or ended is None
                        or instant(ended) >= instant(window["since"])):
                    flags.add(window["issue"])
            except (KeyError, TypeError, ValueError, AttributeError):
                issue = window.get("issue") if isinstance(window, dict) else None
                flags.add(issue if isinstance(issue, str) else "invalid_issue_window")
        if flags <= set(record.get("quality_flags", [])):
            return record
        return {**record, "quality_flags": sorted(set(record.get("quality_flags", [])) | flags)}

    def _retention_warnings(self, records, pins, complete):
        """Operator-visible, non-blocking retention warnings (#106)."""
        live = {r["session_id"] for r in records} | {r["session_id"] for r in self.archive}
        pinned = max(0, len(self.archive) - ARCHIVE_LIMIT)
        warnings = []
        if not complete:
            warnings.append("session_references_unavailable")
        if pinned >= MAX_PINNED - PIN_MARGIN:
            warnings.append("pinned_sessions_near_limit")
        if pins - live:
            warnings.append("referenced_session_not_retained")
        return {"warnings": warnings, "retention": {
            "retained": len(self.archive), "window": ARCHIVE_LIMIT, "max_pinned": MAX_PINNED,
            "pinned_beyond_window": pinned, "referenced": len(pins),
            "referenced_not_retained": len(pins - live), "references_complete": complete}}

    async def _capture_provenance(self, observations, records):
        """Evidence capture only; a fault here never changes or blocks an account."""
        try:
            from .session_review import account_snapshot, digest
            found = await self.hass.async_add_executor_job(
                capture, observations, records, self.sources)
            captured_at = dt_util.utcnow().isoformat()
            changed = False
            for record in records:
                if not record or record["session_id"] not in found:
                    continue
                try:
                    account_digest = digest(account_snapshot(record))
                except Exception:  # noqa: BLE001 - not a settleable account; nothing to link
                    continue
                changed |= self.provenance.observe(
                    record["session_id"], found[record["session_id"]], account_digest, captured_at)
            return changed
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Tariff provenance capture failed; accounts are unaffected")
            return False

    def tariff_provenance(self, session_id, account_digest, detail=False):
        """Read-only: the version captured for exactly this frozen account."""
        version = self.provenance.lookup(session_id, account_digest)
        return version if detail else summary(version)

    async def persist(self):
        await self.store.async_save({
            "observations": copy.deepcopy(self.observations),
            "archive": copy.deepcopy(self.archive),
            "persistent_issues": sorted(self.issues & PERSISTENT_ISSUES),
            "checkpoint_at": dt_util.utcnow().isoformat(),
            "tariff_provenance": self.provenance.stored(),
        })
        self.last_save = time.monotonic()

    async def execute(self, action, data, approving_user_id=None):
        if action == "acknowledge_proxy_issue":
            return await self.acknowledge(data.get("issue"), approving_user_id)
        if action != "refresh":
            raise HomeAssistantError("The sensor proxy is read-only and cannot perform wallet or charger actions")
        await self.async_request_refresh()
        return self.data

    async def acknowledge(self, issue, user_id):
        """Administrator clears one persistent issue; affected sessions stay flagged."""
        if not user_id:
            raise HomeAssistantError("An authenticated HA administrator must acknowledge recorder issues")
        if issue not in ACKNOWLEDGEABLE_ISSUES:
            raise HomeAssistantError("This recorder issue cannot be acknowledged")
        await self.async_refresh()  # Prune first.
        if issue not in self.issues:
            raise HomeAssistantError("That recorder issue is not raised")
        if issue == "observation_limit_reached" and any(
                len(rows) >= MAX_ROWS for rows in self.observations.values()):
            raise HomeAssistantError(
                f"A source still holds {MAX_ROWS} observations; the issue stays until "
                "pruning brings every source below the cap")
        acknowledged_at = dt_util.utcnow().isoformat()
        if issue in WINDOWED_ISSUES:
            since, until = self.issue_window.get(issue, (None, None))
            self.windows = [*self.windows, {
                "issue": issue, "since": since, "until": until or acknowledged_at,
                "acknowledged_at": acknowledged_at, "acknowledged_by": user_id}][-MAX_WINDOWS:]
            self._update_entry(recorder_issue_windows=copy.deepcopy(self.windows))
        self.issues.discard(issue)
        self.issue_window.pop(issue, None)
        _LOGGER.warning("Recorder issue %s acknowledged on proxy %s by HA user %s",
                        issue, self.entry.entry_id, user_id)
        await self.persist()
        await self.async_refresh()
        return self.data

    async def close(self):
        if self.cancel_listener:
            self.cancel_listener()
            self.cancel_listener = None
        await self.persist()
