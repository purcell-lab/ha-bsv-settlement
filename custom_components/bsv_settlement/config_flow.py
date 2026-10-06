"""Choose a read-only recorder/observer or the guarded mainnet operator wallet."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN

BACKENDS = ["sensor_proxy", "ocpp_import_shadow", "embedded_mainnet"]


class BSVSettlementConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OCPPShadowOptionsFlow()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry):
        # Only the read-only observer has options; financial entries have none.
        return config_entry.data.get("backend") == "ocpp_import_shadow"

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            if user_input["backend"] == "ocpp_import_shadow":
                return await self.async_step_ocpp_shadow()
            if user_input["backend"] == "sensor_proxy":
                return await self.async_step_proxy()
            if user_input["backend"] == "embedded_mainnet":
                return await self.async_step_mainnet()
        # Never default to the financial backend.
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("backend", default="sensor_proxy"): vol.In(BACKENDS),
        }))

    async def async_step_ocpp_shadow(self, user_input=None):
        from .ocpp_shadow import METRICS, source_binding, energy
        errors = {}
        if user_input is not None:
            try:
                sources = {k: user_input[k + "_entity"] for k in METRICS}
                binding = source_binding(self.hass, sources)
                state = self.hass.states.get(sources["import"])
                if state is None:
                    raise ValueError("Missing import register")
                energy(state.state, state.attributes.get("unit_of_measurement"))
            except (ValueError, KeyError):
                errors["base"] = "invalid_ocpp_shadow_sources"
            else:
                identity = binding["import"]
                await self.async_set_unique_id(
                    "ocpp-shadow:" + identity["config_entry_id"] + ":" + identity["device_id"])
                self._abort_if_unique_id_configured()
                self._shadow_data = {**user_input, "backend": "ocpp_import_shadow",
                                     "source_binding": binding}
                return await self.async_step_ocpp_shadow_metadata()
        schema = {vol.Optional("name", default="OCPP import shadow"): str}
        schema.update({
            vol.Required(k + "_entity"): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", integration="ocpp")) for k in METRICS})
        return self.async_show_form(step_id="ocpp_shadow", data_schema=vol.Schema(schema), errors=errors)

    async def async_step_ocpp_shadow_metadata(self, user_input=None):
        """Optional fork provenance sensors; skipping keeps the shadow unverified."""
        data = self._shadow_data
        errors = {}
        if user_input is not None:
            try:
                options = shadow_metadata_options(self.hass, data["source_binding"], user_input)
            except ValueError:
                errors["base"] = "invalid_ocpp_shadow_metadata"
            else:
                return self.async_create_entry(
                    title=data.get("name", "OCPP import shadow"), data=data, options=options)
        return self.async_show_form(
            step_id="ocpp_shadow_metadata", errors=errors,
            data_schema=shadow_metadata_schema(self.hass, data["source_binding"], user_input))

    async def async_step_proxy(self, user_input=None):
        errors = {}
        fields = ("import_entity", "export_entity", "state_entity",
                  "import_price_entity", "export_price_entity")
        if user_input is not None:
            states = {field: self.hass.states.get(user_input[field]) for field in fields}
            if not all(states.values()) or len({user_input[f] for f in fields}) != 5:
                errors["base"] = "invalid_proxy_sources"
            elif any(states[f].attributes.get("unit_of_measurement") != "MWh"
                     for f in ("import_entity", "export_entity")) or any(
                         states[f].attributes.get("unit_of_measurement") != "$/kWh"
                         for f in ("import_price_entity", "export_price_entity")):
                errors["base"] = "invalid_proxy_units"
            else:
                await self.async_set_unique_id("sensor-proxy:" + user_input["state_entity"])
                self._abort_if_unique_id_configured()
                from .proxy import SOURCE_KEYS, pin_sources
                # Registry identity pinned at creation (#107); checked on every observation.
                identity = pin_sources(self.hass, {k: user_input[k + "_entity"] for k in SOURCE_KEYS})
                return self.async_create_entry(
                    title=user_input.get("name", "Charging sessions"),
                    data={**user_input, "backend": "sensor_proxy", "source_identity": identity})
        schema = {vol.Optional("name", default="Charging sessions"): str}
        schema.update({
            vol.Required(field): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")) for field in fields
        })
        return self.async_show_form(step_id="proxy", data_schema=vol.Schema(schema), errors=errors)

    async def async_step_mainnet(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not all(user_input.get(k) is True for k in (
                "acknowledge_key_custody", "acknowledge_mainnet", "enable_broadcast")):
                errors["base"] = "acknowledgement_required"
            else:
                await self.async_set_unique_id("embedded-operator-mainnet")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="BSV Operator Wallet (Mainnet)",
                    data={"backend": "embedded_mainnet", "network": "mainnet",
                          "acknowledge_key_custody": True, "acknowledge_mainnet": True,
                          "enable_broadcast": True})
        return self.async_show_form(step_id="mainnet", errors=errors, data_schema=vol.Schema({
            vol.Required("acknowledge_key_custody", default=False): bool,
            vol.Required("acknowledge_mainnet", default=False): bool,
            vol.Required("enable_broadcast", default=False): bool,
        }))


def shadow_metadata_options(hass, binding, user_input):
    from .ocpp_shadow import METADATA_FIELDS, metadata_binding
    selected = {k: user_input.get(field) for k, field in METADATA_FIELDS.items()}
    bound = metadata_binding(hass, binding, selected)
    return {**{METADATA_FIELDS[k]: v["entity_id"] for k, v in bound.items()},
            "metadata_binding": bound}


def shadow_metadata_schema(hass, binding, current=None):
    """Prefill exact unique-ID matches, else the current choice; all optional."""
    from .ocpp_shadow import METADATA_FIELDS, suggest_metadata
    found = suggest_metadata(hass, binding)
    return vol.Schema({
        vol.Optional(field, description={"suggested_value": (
            current.get(field) if current is not None else found.get(key))}):
        selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", integration="ocpp"))
        for key, field in METADATA_FIELDS.items()})


EXPORT_ERRORS = ("invalid_ocpp_export_sources", "invalid_ocpp_export_reference",
                 "invalid_ocpp_export_grading")


def shadow_export_options(hass, binding, user_input):
    """Validated export options; ValueError(args[0]) names the form error."""
    from .ocpp_export_shadow_ledger import grading_rules
    from .ocpp_shadow import (EXPORT_FIELDS, GRADING_FIELDS, REFERENCE_FIELD,
                              export_binding, reference_binding)
    try:
        bound = export_binding(hass, binding, {k: user_input.get(f) for k, f in EXPORT_FIELDS.items()})
    except ValueError:
        raise ValueError(EXPORT_ERRORS[0]) from None
    try:
        reference = reference_binding(hass, binding, bound, user_input.get(REFERENCE_FIELD))
    except ValueError:
        raise ValueError(EXPORT_ERRORS[1]) from None
    try:
        grading = grading_rules({k: user_input.get(f) for k, f in GRADING_FIELDS.items()})
    except ValueError:
        raise ValueError(EXPORT_ERRORS[2]) from None
    return {**{EXPORT_FIELDS[k]: v["entity_id"] for k, v in bound.items()},
            **({REFERENCE_FIELD: reference["entity_id"]} if reference else {}),
            **{field: float(grading[k]) for k, field in GRADING_FIELDS.items()},
            "export_binding": bound, "export_reference_binding": reference,
            "export_grading": grading}


def shadow_export_schema(hass, binding, current=None, grading=None):
    """Prefill exact unique-ID export matches, else the current choice.

    The reference counter is never prefilled; thresholds default to the stored
    or built-in grading rules.
    """
    from .ocpp_export_shadow_ledger import grading_rules
    from .ocpp_shadow import EXPORT_FIELDS, GRADING_FIELDS, REFERENCE_FIELD, suggest_export
    found = suggest_export(hass, binding)
    rules = grading_rules(grading)
    schema = {
        vol.Optional(field, description={"suggested_value": (
            current.get(field) if current is not None else found.get(key))}):
        selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", integration="ocpp"))
        for key, field in EXPORT_FIELDS.items()}
    schema[vol.Optional(REFERENCE_FIELD, description={"suggested_value": (
        current or {}).get(REFERENCE_FIELD)})] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor"))
    limits = {"min_energy_kwh": (0.001, 100, "kWh"), "tolerance_pct": (0.01, 100, "%"),
              "wide_pct": (0.01, 100, "%")}
    for key, field in GRADING_FIELDS.items():
        low, high, unit = limits[key]
        value = (current or {}).get(field, float(rules[key]))
        schema[vol.Optional(field, default=value)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=low, max=high, step="any", unit_of_measurement=unit,
                                          mode=selector.NumberSelectorMode.BOX))
    return vol.Schema(schema)


RECONCILE_ERRORS = ("invalid_recorder_link", "invalid_reconciliation_tolerances")
NO_LINK = "none"
RECONCILE_FIELDS = {"import_pct": "reconcile_import_tolerance_pct",
                    "import_floor_kwh": "reconcile_import_floor_kwh",
                    "export_pct": "reconcile_export_tolerance_pct",
                    "export_floor_kwh": "reconcile_export_floor_kwh",
                    "min_energy_kwh": "reconcile_min_energy_kwh"}
RECONCILE_KEYS = ("legacy_proxy_entry_id", "reconciliation_tolerances", *RECONCILE_FIELDS.values())


def shadow_reconcile_options(hass, user_input):
    """Validated link and tolerances; ValueError(args[0]) names the form error."""
    from .recorder import LINK_OPTION, TOLERANCE_OPTION, proxy_entries
    from .recorder_reconciliation import tolerance_rules
    chosen = user_input.get(LINK_OPTION)
    if chosen != NO_LINK and chosen not in proxy_entries(hass):
        raise ValueError(RECONCILE_ERRORS[0])
    try:
        rules = tolerance_rules({k: user_input.get(f) for k, f in RECONCILE_FIELDS.items()})
    except ValueError:
        raise ValueError(RECONCILE_ERRORS[1]) from None
    return {LINK_OPTION: None if chosen == NO_LINK else chosen,
            **{field: float(rules[k]) for k, field in RECONCILE_FIELDS.items()},
            TOLERANCE_OPTION: rules}


def shadow_reconcile_schema(hass, options, current=None):
    """Default to the stored link, else the only sensor_proxy entry; never guess among several."""
    from .recorder import LINK_OPTION, TOLERANCE_OPTION, proxy_entries
    from .recorder_reconciliation import tolerance_rules
    entries = proxy_entries(hass)
    stored = options.get(LINK_OPTION, "")
    if current is not None:
        default = current.get(LINK_OPTION)
    elif LINK_OPTION in options:
        default = stored if stored in entries else NO_LINK if stored is None else None
    else:
        default = next(iter(entries)) if len(entries) == 1 else None
    choices = {**entries, NO_LINK: "Do not reconcile"}
    key = (vol.Required(LINK_OPTION, default=default) if default in choices
           else vol.Required(LINK_OPTION))
    try:
        rules = tolerance_rules(options.get(TOLERANCE_OPTION))
    except ValueError:
        rules = tolerance_rules()
    schema = {key: vol.In(choices)}
    limits = {"import_pct": (0.01, 100, "%"), "import_floor_kwh": (0, 10, "kWh"),
              "export_pct": (0.01, 100, "%"), "export_floor_kwh": (0, 10, "kWh"),
              "min_energy_kwh": (0.001, 100, "kWh")}
    for name, field in RECONCILE_FIELDS.items():
        low, high, unit = limits[name]
        value = (current or {}).get(field, float(rules[name]))
        schema[vol.Optional(field, default=value)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=low, max=high, step="any", unit_of_measurement=unit,
                                          mode=selector.NumberSelectorMode.BOX))
    return vol.Schema(schema)


class OCPPShadowOptionsFlow(config_entries.OptionsFlowWithReload):
    """Bind/unbind optional provenance metadata and export observation, then
    link the legacy recorder for reconciliation (shown only when one exists).

    The three measurand sources and their frozen binding are not editable here.
    """

    async def async_step_init(self, user_input=None):
        binding = self.config_entry.data["source_binding"]
        errors = {}
        if user_input is not None:
            try:
                options = shadow_metadata_options(self.hass, binding, user_input)
            except ValueError:
                errors["base"] = "invalid_ocpp_shadow_metadata"
            else:
                self._metadata_options = options
                return await self.async_step_export()
        current = user_input
        if current is None and self.config_entry.options.get("metadata_binding"):
            current = dict(self.config_entry.options)
        return self.async_show_form(
            step_id="init", errors=errors,
            data_schema=shadow_metadata_schema(self.hass, binding, current))

    async def async_step_export(self, user_input=None):
        """Optional read-only export shadow; disabled until the register is bound."""
        binding = self.config_entry.data["source_binding"]
        options = self.config_entry.options
        errors = {}
        if user_input is not None:
            try:
                export = shadow_export_options(self.hass, binding, user_input)
            except ValueError as err:
                errors["base"] = str(err) if str(err) in EXPORT_ERRORS else EXPORT_ERRORS[0]
            else:
                self._export_options = export
                from .recorder import proxy_entries
                if proxy_entries(self.hass):
                    return await self.async_step_reconcile()
                # No legacy recorder to link: keep any earlier reconciliation choice.
                kept = {k: v for k, v in options.items() if k in RECONCILE_KEYS}
                return self.async_create_entry(data={**self._metadata_options, **export, **kept})
        current = user_input
        if current is None and options.get("export_binding"):
            current = dict(options)
        return self.async_show_form(
            step_id="export", errors=errors, data_schema=shadow_export_schema(
                self.hass, binding, current, options.get("export_grading")))

    async def async_step_reconcile(self, user_input=None):
        """Link the legacy recorder to compare against, and tolerances. Read-only."""
        errors = {}
        if user_input is not None:
            try:
                reconcile = shadow_reconcile_options(self.hass, user_input)
            except ValueError as err:
                errors["base"] = str(err) if str(err) in RECONCILE_ERRORS else RECONCILE_ERRORS[0]
            else:
                return self.async_create_entry(
                    data={**self._metadata_options, **self._export_options, **reconcile})
        return self.async_show_form(step_id="reconcile", errors=errors,
                                    data_schema=shadow_reconcile_schema(
                                        self.hass, self.config_entry.options, user_input))
