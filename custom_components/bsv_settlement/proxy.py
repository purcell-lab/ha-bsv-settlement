"""Persistent read-only HA recorder, isolated from wallet and charger actions."""
import copy
from datetime import timedelta
from functools import partial
import logging
import time

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .proxy_ledger import build_records, instant

_LOGGER = logging.getLogger(__name__)
SOURCE_KEYS = ("import", "export", "state", "import_price", "export_price")
MAX_ROWS = 60000


def normalize(state):
    attrs = state.attributes
    return {
        "t": dt_util.as_local(state.last_updated).isoformat(), "value": state.state,
        **({"unit": attrs["unit_of_measurement"]} if "unit_of_measurement" in attrs else {}),
        **{key: attrs[field] for key, field in (
            ("start", "start_time"), ("end", "end_time"), ("estimate", "estimate"))
           if field in attrs},
    }


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
        self.store = Store(hass, 1, f"{DOMAIN}.proxy.{entry.entry_id}")
        self.observations = {key: [] for key in SOURCE_KEYS}
        self.archive = []
        self.issues = set()
        self.cancel_listener = None
        self.last_save = 0
        self.last_latest_id = None

    async def load(self):
        saved = await self.store.async_load()
        if saved:
            self.observations = saved["observations"]
            self.archive = saved.get("archive", [])
            self.issues.update(saved.get("persistent_issues", []))
        end = dt_util.utcnow()
        start = end - timedelta(hours=24)
        if saved and saved.get("checkpoint_at"):
            start = max(start, dt_util.parse_datetime(saved["checkpoint_at"]) - timedelta(minutes=5))
            if dt_util.parse_datetime(saved["checkpoint_at"]) < end - timedelta(hours=24):
                self.issues.add("restart_gap_exceeds_24_hour_backfill")
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import get_significant_states
            # Attribute-only updates carry tariff effective periods and finality.
            history = await get_instance(self.hass).async_add_executor_job(partial(
                get_significant_states, self.hass, start, end,
                entity_ids=list(self.sources.values()), significant_changes_only=False,
                minimal_response=False, no_attributes=False))
            for key, entity in self.sources.items():
                rows = [normalize(state) for state in history.get(entity, [])]
                if not rows:
                    self.issues.add(key + ":history_unavailable")
                self.observations[key] = merge_rows(self.observations[key], rows)
        except Exception:
            _LOGGER.warning("Proxy history backfill unavailable; observation remains provisional")
            self.issues.add("history_backfill_failed")
        # From this point there is no await before listener registration and snapshot.
        self.cancel_listener = async_track_state_change_event(
            self.hass, list(self.sources.values()), self._state_changed)
        self.snapshot_current()

    @callback
    def _state_changed(self, event):
        state = event.data.get("new_state")
        if state:
            for key, entity in self.sources.items():
                if entity == state.entity_id:
                    self._append(key, normalize(state))

    @callback
    def _append(self, key, row):
        rows = self.observations[key]
        if len(rows) >= MAX_ROWS:
            self.issues.add("observation_limit_reached")
            return
        if rows and row == rows[-1]:
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
            if state:
                self._append(key, normalize(state))

    async def _async_update_data(self):
        self.snapshot_current()
        as_of = dt_util.now().isoformat()
        observations = copy.deepcopy(self.observations)
        records = await self.hass.async_add_executor_job(
            build_records, observations, self.sources["state"], as_of)
        unavailable = [
            key for key, entity in self.sources.items()
            if (state := self.hass.states.get(entity)) is None
            or state.state in ("unknown", "unavailable")
        ]
        latest = records[-1] if records else None
        previous = records[-2] if len(records) > 1 else (self.archive[-1] if self.archive else None)
        issues = sorted(self.issues | {key + ":unavailable" for key in unavailable})
        if issues and latest:
            latest = {**latest, "net_cost_aud": None, "net_cost_aud_unrounded": None,
                      "quality_flags": sorted(set(latest["quality_flags"]) | set(issues))}
        # Keep latest ended session plus current/most recent session for repricing.
        # Keep the preceding baseline for each source and a small summary archive.
        if len(records) > 2:
            cut = instant(records[-2]["opened_at"])
            known = {r["session_id"]: r for r in self.archive}
            known.update({r["session_id"]: r for r in records[:-2]})
            self.archive = list(known.values())[-50:]
            for key, rows in self.observations.items():
                before = [r for r in rows if instant(r["t"]) < cut]
                after = [r for r in rows if instant(r["t"]) >= cut]
                self.observations[key] = before[-1:] + after
        current_id = latest["session_id"] if latest else None
        if current_id != self.last_latest_id or time.monotonic() - self.last_save >= 300:
            await self.persist()
            self.last_latest_id = current_id
        return {
            "state": "degraded" if issues else "recording" if latest and not latest["ended_at"] else "waiting",
            "updated_at": as_of, "latest_session": latest, "previous_session": previous,
            "issues": issues, "source_entities": self.sources, "billing_eligible": False,
        }

    async def persist(self):
        await self.store.async_save({
            "observations": copy.deepcopy(self.observations),
            "archive": copy.deepcopy(self.archive),
            "persistent_issues": sorted(self.issues & {
                "observation_limit_reached", "restart_gap_exceeds_24_hour_backfill"}),
            "checkpoint_at": dt_util.utcnow().isoformat(),
        })
        self.last_save = time.monotonic()

    async def execute(self, action, data, approving_user_id=None):
        if action != "refresh":
            raise HomeAssistantError("The sensor proxy is read-only and cannot perform wallet or charger actions")
        await self.async_request_refresh()
        return self.data

    async def close(self):
        if self.cancel_listener:
            self.cancel_listener()
            self.cancel_listener = None
        await self.persist()
