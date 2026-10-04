"""Source-neutral, read-only recorder readiness contract (no selector, no switching).

Implements the logical ``RecorderAdapter`` capabilities/health part of
docs/ocpp-recorder-investigation.md as read-only facades over the existing
legacy Sigenergy ``ProxyCoordinator`` and the OCPP shadow coordinator. Nothing
here creates accounts, prices energy, changes which recorder settles, calls a
service or commands a charger. The settlement recorder stays ``legacy_sigen``.

Readiness ladder, per direction (import/export):

    unavailable -> entity_enabled -> numeric_reading
      -> fresh_attributable_sample -> validated_directional_accounting

``validated_directional_accounting`` is never reached automatically. It needs a
persisted reconciliation result within tolerance AND an explicit operator
validation flag; no flag is provided or set by this module
(``OPERATOR_VALIDATION_AVAILABLE`` is False), and estimated export can never
pass ``fresh_attributable_sample``.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Protocol

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .ocpp_export_shadow_ledger import DERIVED_SOURCE
from .ocpp_shadow_ledger import energy, instant, provenance_flags
from .proxy_ledger import ACTIVE as LEGACY_ACTIVE, PREPARING as LEGACY_PREPARING
from .proxy_ledger import TERMINAL as LEGACY_TERMINAL, number
from .recorder_reconciliation import (
    DIRECTIONS, PENDING_LIMIT_SECONDS, SCHEMA, SETTLE_SECONDS, SETTLEMENT_OWNER,
    ReconciliationLedger, reconcile_span, tolerance_rules)

_LOGGER = logging.getLogger(__name__)
LEVELS = ("unavailable", "entity_enabled", "numeric_reading",
          "fresh_attributable_sample", "validated_directional_accounting")
KINDS = ("legacy_sigen", "ocpp")
# The only settlement recorder. Read from code/config, never switched here.
SETTLEMENT_RECORDER = SETTLEMENT_OWNER
LEGACY_CADENCE_S = 5
OCPP_DEFAULT_CADENCE_S = 60
FUTURE_TOLERANCE_S = 5
# Future work: an operator-confirmed validation record. Not implemented.
OPERATOR_VALIDATION_AVAILABLE = False
LINK_OPTION = "legacy_proxy_entry_id"
TOLERANCE_OPTION = "reconciliation_tolerances"
UNAVAILABLE = ("", "unknown", "unavailable", "none")


def freshness_limit(cadence):
    """A sample is fresh while its age is at most max(3 x cadence, cadence + 30 s)."""
    return max(3 * cadence, cadence + 30)


@dataclass(frozen=True)
class Evidence:
    """What a facade observed for one direction; input to the ladder."""
    entity_id: str | None
    present: bool = False       # registered and enabled (or unregistered with a state)
    available: bool = False     # state is not unknown/unavailable
    numeric: bool = False       # finite, non-negative reading in a supported unit
    sample_at: datetime | None = None
    attributable: bool = False  # accepted by the recorder for this connector/direction
    estimated: bool = False
    limitations: tuple = ()


@dataclass(frozen=True)
class Validation:
    reconciled_within_tolerance: bool = False
    operator_confirmed: bool = False


@dataclass(frozen=True)
class DirectionCapability:
    direction: str
    level: str
    limitations: tuple
    entity_id: str | None
    last_sample_at: str | None
    sample_age_s: float | None
    expected_cadence_s: int
    freshness_limit_s: int

    def as_dict(self):
        return {"level": self.level, "limitations": list(self.limitations),
                "entity_id": self.entity_id, "last_sample_at": self.last_sample_at,
                "sample_age_s": self.sample_age_s, "expected_cadence_s": self.expected_cadence_s,
                "freshness_limit_s": self.freshness_limit_s}


@dataclass(frozen=True)
class RecorderStatus:
    recorder_kind: str
    recorder_id: str | None
    role: str                   # settlement_source | shadow
    health: str                 # ready | shadow_only | stale | degraded | unavailable | incompatible
    capabilities: tuple         # DirectionCapability per direction
    limitations: tuple = ()
    settlement_owner: bool = False

    @property
    def overall_level(self):
        return min((c.level for c in self.capabilities), key=LEVELS.index, default=LEVELS[0])

    def capability(self, direction):
        return next(c for c in self.capabilities if c.direction == direction)

    def as_dict(self):
        return {"recorder_kind": self.recorder_kind, "recorder_id": self.recorder_id,
                "role": self.role, "health": self.health, "overall_level": self.overall_level,
                "directions": {c.direction: c.as_dict() for c in self.capabilities},
                "limitations": list(self.limitations),
                "settlement_owner": self.settlement_owner, "billing_eligible": False}


class RecorderAdapter(Protocol):
    """Read-only contract shared by the legacy and OCPP facades."""
    kind: str
    recorder_id: str | None

    def status(self, now: datetime, validations: dict | None = None) -> RecorderStatus: ...


def assess(direction, evidence, now, cadence, validation=None):
    """Place one direction on the ladder. Validation can only lift a fresh,
    attributable, non-estimated sample, and only with operator confirmation."""
    limitations = set(evidence.limitations)
    limit = freshness_limit(cadence)
    age = (now - evidence.sample_at).total_seconds() if evidence.sample_at else None
    if not evidence.present:
        level = "unavailable"
    elif not evidence.available:
        level = "entity_enabled"
        limitations.add("reading_unavailable")
    elif not evidence.numeric:
        level = "entity_enabled"
        limitations.add("non_numeric_reading")
    elif age is None or age > limit or age < -FUTURE_TOLERANCE_S:
        level = "numeric_reading"
        limitations.add("stale_sample")
    elif not evidence.attributable:
        level = "numeric_reading"
        limitations.add("sample_not_attributable")
    else:
        level = "fresh_attributable_sample"
    if evidence.estimated:
        limitations.add("export_estimated" if direction == "export" else "estimated")
    if (level == "fresh_attributable_sample" and not evidence.estimated and validation
            and validation.reconciled_within_tolerance and validation.operator_confirmed):
        level = "validated_directional_accounting"
    else:
        limitations.add("directional_accounting_not_validated")
    return DirectionCapability(
        direction, level, tuple(sorted(limitations)), evidence.entity_id,
        evidence.sample_at.isoformat() if evidence.sample_at else None,
        round(age, 1) if age is not None else None, int(cadence), int(limit))


def _sample_at(state):
    reported = getattr(state, "last_reported", None)
    return max(state.last_updated, reported) if reported else state.last_updated


def _registry_row(hass, entity_id):
    try:
        return er.async_get(hass).async_get(entity_id)
    except (KeyError, AttributeError):
        return None


class LegacySigenRecorder:
    """Read-only facade over a loaded sensor_proxy ``ProxyCoordinator``."""
    kind = "legacy_sigen"

    def __init__(self, hass, coordinator):
        self.hass, self.coordinator = hass, coordinator
        self.recorder_id = coordinator.entry.entry_id

    def evidence(self, direction):
        entity_id = self.coordinator.sources[direction]
        state = self.hass.states.get(entity_id)
        row = _registry_row(self.hass, entity_id)
        limitations = set() if row is not None else {"entity_not_registered"}
        issues = set(self.coordinator.issues)
        present = state is not None and (row is None or row.disabled_by is None)
        if not present:
            return Evidence(entity_id, limitations=tuple(limitations))
        available = str(state.state).lower() not in UNAVAILABLE
        value = number(state.state) if available else None
        numeric = (value is not None and value >= 0
                   and state.attributes.get("unit_of_measurement") == "MWh")
        running = self.hass.states.get(self.coordinator.sources["state"])
        known = running is not None and running.state in (
            LEGACY_ACTIVE | LEGACY_PREPARING | LEGACY_TERMINAL)
        if not known:
            limitations.add("running_state_unknown")
        if "observation_limit_reached" in issues:
            limitations.add("legacy_observation_limit_reached")
        return Evidence(entity_id, True, available, numeric, _sample_at(state),
                        known and "observation_limit_reached" not in issues,
                        False, tuple(limitations))

    def status(self, now, validations=None):
        capabilities = tuple(assess(d, self.evidence(d), now, LEGACY_CADENCE_S)
                             for d in DIRECTIONS)
        levels = [c.level for c in capabilities]
        state = (self.coordinator.data or {}).get("state")
        health = ("unavailable" if "unavailable" in levels
                  else "stale" if any("stale_sample" in c.limitations for c in capabilities)
                  else "degraded" if state == "degraded" else "ready")
        return RecorderStatus(self.kind, self.recorder_id, "settlement_source", health,
                              capabilities, ("sensor_proxy_provisional_ids",),
                              settlement_owner=True)

    def readings(self, direction):
        """Sorted (datetime, kWh | None) cumulative readings, plus the current
        state at its last report time (Sigen reports unchanged values too)."""
        rows = []
        for row in self.coordinator.observations.get(direction, []):
            try:
                stamp = instant(row["t"])
            except (KeyError, TypeError, ValueError):
                continue
            value = number(row.get("value"))
            valid = row.get("unit") == "MWh" and value is not None and value >= 0
            rows.append((stamp, value * 1000 if valid else None))
        rows.sort(key=lambda item: item[0])
        state = self.hass.states.get(self.coordinator.sources[direction])
        if state is not None and state.attributes.get("unit_of_measurement") == "MWh":
            value = number(state.state)
            stamp = _sample_at(state)
            if value is not None and value >= 0 and (not rows or stamp > rows[-1][0]):
                rows.append((stamp, value * 1000))
        return rows

    def flags(self):
        return sorted(self.coordinator.issues)


class OCPPShadowRecorder:
    """Read-only facade over the OCPP shadow coordinator; never a settlement source."""
    kind = "ocpp"

    def __init__(self, hass, coordinator):
        self.hass, self.coordinator = hass, coordinator
        self.recorder_id = coordinator.entry.entry_id

    def cadence(self):
        interval = self.coordinator.ledger.provenance.get("meter_value_sample_interval_s")
        return interval if type(interval) is int else OCPP_DEFAULT_CADENCE_S

    def _reading(self, entity_id):
        state = self.hass.states.get(entity_id)
        if state is None:
            return None, False, False
        available = str(state.state).lower() not in UNAVAILABLE
        try:
            energy(state.state, state.attributes.get("unit_of_measurement"))
            numeric = available
        except ValueError:
            numeric = False
        return state, available, numeric

    def import_evidence(self):
        coord = self.coordinator
        entity_id = coord.sources["import"]
        ledger = coord.ledger
        limitations = {"entity_observation_only", "source_timestamp_unavailable",
                       *provenance_flags(ledger.provenance), *ledger.flags}
        if type(ledger.provenance.get("meter_value_sample_interval_s")) is not int:
            limitations.add("sample_interval_unverified")
        if coord.binding_error:
            return Evidence(entity_id, limitations=tuple(sorted(
                limitations | {"source_binding_changed"})))
        state, available, numeric = self._reading(entity_id)
        if state is None:
            return Evidence(entity_id, limitations=tuple(sorted(limitations)))
        attributable = ledger.data["current"] is not None and not ledger.flags
        return Evidence(entity_id, True, available, numeric, _sample_at(state), attributable,
                        False, tuple(sorted(limitations)))

    def export_evidence(self):
        coord = self.coordinator
        if not coord.export_binding:
            return Evidence(None, limitations=("export_not_bound",))
        entity_id = coord.export_sources.get("register")
        if coord.export_error:
            return Evidence(entity_id, limitations=("export_binding_changed",))
        export = coord.export
        limitations = {"entity_observation_only", *(export.flags if export else ())}
        state, available, numeric = self._reading(entity_id)
        if state is None:
            return Evidence(entity_id, limitations=tuple(sorted(limitations)))
        estimated = state.attributes.get("source") == DERIVED_SOURCE
        if not estimated:
            limitations.add("native_export_unverified")
        attributable = bool(export and export.data["current"] is not None and not export.flags)
        return Evidence(entity_id, True, available, numeric, _sample_at(state), attributable,
                        estimated, tuple(sorted(limitations)))

    def status(self, now, validations=None):
        validations = validations or {}
        cadence = self.cadence()
        capabilities = (
            assess("import", self.import_evidence(), now, cadence, validations.get("import")),
            assess("export", self.export_evidence(), now, cadence, validations.get("export")))
        health = ("incompatible" if self.coordinator.binding_error
                  else "unavailable" if all(c.level == "unavailable" for c in capabilities)
                  else "shadow_only")
        return RecorderStatus(self.kind, self.recorder_id, "shadow", health, capabilities,
                              ("not_settlement_source", "selector_not_implemented"))


class ReconciliationStore(Store):
    """Separate store; there is no earlier schema, so any other version is refused."""

    def __init__(self, hass, entry_id):
        super().__init__(hass, SCHEMA, f"{DOMAIN}.recorder_reconciliation.{entry_id}")

    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        raise ValueError("Unsupported recorder reconciliation store version")


def proxy_entries(hass):
    """{entry_id: title} of configured sensor_proxy (legacy Sigenergy) entries."""
    try:
        entries = hass.config_entries.async_entries(DOMAIN)
    except AttributeError:
        return {}
    return {e.entry_id: e.title for e in entries if e.data.get("backend") == "sensor_proxy"}


def resolve_link(hass, options):
    """(legacy entry id | None, link state). Auto-detect only one unambiguous entry."""
    entries = proxy_entries(hass)
    if LINK_OPTION in options:
        chosen = options[LINK_OPTION]
        if chosen is None:
            return None, "not_linked"
        return (chosen, "configured") if chosen in entries else (None, "invalid")
    if len(entries) == 1:
        return next(iter(entries)), "auto_detected"
    return None, "ambiguous" if entries else "no_legacy_recorder"


class RecorderMonitor:
    """Readiness and reconciliation for one OCPP shadow entry. Read-only."""

    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.store = ReconciliationStore(hass, entry.entry_id)
        self.ledger = None
        self.state = "not_loaded"
        try:
            self.tolerances = tolerance_rules(entry.options.get(TOLERANCE_OPTION))
            self.tolerance_error = False
        except ValueError:
            self.tolerances, self.tolerance_error = tolerance_rules(), True

    async def load(self, now):
        """Refusal degrades reconciliation only; the refused file is never rewritten."""
        try:
            saved = await self.store.async_load()
            self.ledger = ReconciliationLedger(saved, started_at=now)
        except (ValueError, KeyError, TypeError, HomeAssistantError):
            _LOGGER.warning("Recorder reconciliation store refused; comparisons disabled")
            self.ledger, self.state = None, "store_refused"
            return
        self.state = "ready"
        if saved is None:
            self.save()

    def save(self):
        if self.ledger is not None:
            self.store.async_delay_save(lambda: deepcopy(self.ledger.data), 1)

    def legacy(self, entry_id):
        coordinator = self.hass.data.get(DOMAIN, {}).get(entry_id) if entry_id else None
        if coordinator is None or getattr(coordinator, "mode", None) != "sensor_proxy":
            return None
        return LegacySigenRecorder(self.hass, coordinator)

    def reconcile(self, coordinator, now):
        """Compare newly closed, settled spans; returns True when a result changed.

        Never lets a reconciliation fault interrupt shadow observation.
        """
        if self.ledger is None:
            return False
        try:
            return self._reconcile(coordinator, now)
        except Exception:  # noqa: BLE001 - diagnostic path must not break observation
            _LOGGER.exception("Recorder reconciliation failed; observation continues")
            return False

    def _reconcile(self, coordinator, now):
        clock = instant(now)
        link, _ = resolve_link(self.hass, self.entry.options)
        legacy = self.legacy(link)
        cache, changed = {}, False
        sections = (("import", coordinator.ledger.data["spans"]),
                    ("export", coordinator.export.data["spans"] if coordinator.export else []))
        for direction, spans in sections:
            for span in self.ledger.candidates(direction, spans):
                if link is None:
                    self.ledger.skip(direction, span)
                    changed = True
                    continue
                waited = (clock - instant(span["observation_ended_at"])).total_seconds()
                if waited < SETTLE_SECONDS or (legacy is None and waited < PENDING_LIMIT_SECONDS):
                    break
                if legacy is not None and direction not in cache:
                    cache[direction] = legacy.readings(direction)
                result = reconcile_span(
                    span, direction, cache.get(direction), self.tolerances, link, now,
                    legacy.flags() if legacy else ("legacy_recorder_not_loaded",))
                self.ledger.record(direction, span, result)
                changed = True
        if changed:
            self.save()
        return changed

    def pending(self, coordinator):
        if self.ledger is None:
            return 0
        return (len(self.ledger.candidates("import", coordinator.ledger.data["spans"]))
                + len(self.ledger.candidates(
                    "export", coordinator.export.data["spans"] if coordinator.export else [])))

    def validations(self):
        """Per direction: reconciled within tolerance AND operator confirmed (never, here)."""
        result = {}
        for direction in DIRECTIONS:
            last = self.ledger.last(direction) if self.ledger else None
            # operator_confirmed stays False: no operator validation flag exists yet.
            result[direction] = Validation(bool(last and last["within_tolerance"]), False)
        return result

    def summary(self, coordinator, now):
        clock = instant(now)
        link, link_state = resolve_link(self.hass, self.entry.options)
        legacy = self.legacy(link)
        ocpp = OCPPShadowRecorder(self.hass, coordinator).status(clock, self.validations())
        reconciliation = self.ledger.summary() if self.ledger else {}
        flags = []
        if self.tolerance_error:
            flags.append("tolerance_options_invalid_defaults_used")
        if link is not None and legacy is None:
            flags.append("legacy_recorder_not_loaded")
        return {
            "settlement_recorder": SETTLEMENT_RECORDER if link else "not_linked",
            "legacy_entry_id": link, "link_state": link_state,
            "ocpp": ocpp.as_dict(),
            "legacy": legacy.status(clock).as_dict() if legacy else None,
            "reconciliation": {**reconciliation, "state": self.state,
                               "pending_count": self.pending(coordinator),
                               "tolerance": dict(self.tolerances), "flags": flags},
            "selector_implemented": False,
            "operator_validation_available": OPERATOR_VALIDATION_AVAILABLE,
            "billing_eligible": False, "settlement_owner": SETTLEMENT_RECORDER,
        }

    async def close(self):
        if self.ledger is not None:
            await self.store.async_save(deepcopy(self.ledger.data))
