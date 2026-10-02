"""Mock-only BSV session settlement integration."""
import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WalletAPI
from .const import DOMAIN, SERVICES
from .coordinator import SettlementCoordinator

PLATFORMS = [Platform.SENSOR]


async def async_setup(hass, config):
    hass.data.setdefault(DOMAIN, {})
    common = {vol.Required("config_entry_id"): str}
    per_session = {**common, vol.Required("session_id"): vol.All(str, vol.Length(min=1, max=200))}
    schemas = {
        "bind_session": {**per_session, vol.Required("started_at"): str,
                         vol.Required("driver_binding_id", default="driver-demo-01"): vol.In(["driver-demo-01"])},
        "add_interval": {**per_session, vol.Required("interval"): dict},
        "prepare_session": {**per_session, vol.Required("ended_at"): str,
                            vol.Required("final_import_wh"): vol.All(int, vol.Range(min=0)),
                            vol.Required("final_export_wh"): vol.All(int, vol.Range(min=0))},
        "request_payment": per_session,
        "refresh": common,
    }

    async def handle(call):
        coordinator = hass.data[DOMAIN].get(call.data["config_entry_id"])
        if coordinator is None:
            raise HomeAssistantError("The selected BSV mock config entry is not loaded")
        result = await coordinator.execute(call.service, call.data)
        return result if call.return_response else None

    for name in SERVICES:
        hass.services.async_register(DOMAIN, name, handle, schema=vol.Schema(schemas[name]),
                                     supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_setup_entry(hass, entry):
    api = WalletAPI(async_get_clientsession(hass), entry.data["service_url"], entry.data["api_token"])
    coordinator = SettlementCoordinator(hass, entry, api)
    await coordinator.load()
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry):
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
