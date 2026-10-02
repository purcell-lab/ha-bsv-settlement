"""Mock settlement and isolated, broadcast-disabled embedded operator wallet."""
import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.importlib import async_import_module

from .api import WalletAPI
from .const import DOMAIN, SERVICES
from .coordinator import SettlementCoordinator

PLATFORMS = [Platform.SENSOR, Platform.TEXT]


async def async_setup(hass, config):
    hass.data.setdefault(DOMAIN, {})
    common = {vol.Required("config_entry_id"): str}
    per_session = {**common, vol.Required("session_id"): vol.All(str, vol.Length(min=1, max=200))}
    schemas = {
        "bind_session": {**per_session, vol.Required("started_at"): str,
                         vol.Required("driver_binding_id", default="driver-demo-01"): vol.In(
                             ["driver-demo-01", "driver-external"])},
        "add_interval": {**per_session, vol.Required("interval"): dict},
        "prepare_session": {**per_session, vol.Required("ended_at"): str,
                            vol.Required("final_import_wh"): vol.All(int, vol.Range(min=0)),
                            vol.Required("final_export_wh"): vol.All(int, vol.Range(min=0))},
        "request_payment": per_session,
        "refresh": common,
        "wallet_status": common,
        "wallet_self_test": common,
        "wallet_refresh_chain": common,
        "prepare_operator_payment": {
            **common, vol.Required("reference"): vol.All(str, vol.Length(min=6, max=100)),
            vol.Required("amount_sats"): vol.All(int, vol.Range(min=1, max=100000)),
            vol.Required("fee_sats"): vol.All(int, vol.Range(min=1, max=1000)),
        },
        "broadcast_operator_payment": {
            **common, vol.Required("draft_id"): str,
            vol.Required("recipient_address"): str,
            vol.Required("amount_sats"): vol.All(int, vol.Range(min=1, max=100000)),
            vol.Required("fee_sats"): vol.All(int, vol.Range(min=1, max=1000)),
            vol.Required("confirm_mainnet_payment"): vol.In([True]),
        },
        "cancel_operator_payment": {**common, vol.Required("draft_id"): str},
    }

    async def handle(call):
        if call.service in ("prepare_operator_payment", "broadcast_operator_payment",
                            "cancel_operator_payment", "wallet_refresh_chain"):
            # Unlike the generic admin wrapper, refuse context-free automation.
            user = (await hass.auth.async_get_user(call.context.user_id)
                    if call.context.user_id else None)
            if user is None or not user.is_admin:
                raise HomeAssistantError("An authenticated HA administrator must perform this wallet action")
        coordinator = hass.data[DOMAIN].get(call.data["config_entry_id"])
        if coordinator is None:
            raise HomeAssistantError("The selected BSV config entry is not loaded")
        result = await coordinator.execute(call.service, call.data, call.context.user_id)
        return result if call.return_response else None

    for name in SERVICES:
        hass.services.async_register(DOMAIN, name, handle, schema=vol.Schema(schemas[name]),
                                     supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_setup_entry(hass, entry):
    if entry.data.get("backend") in ("embedded_testnet", "embedded_mainnet"):
        from .api import WalletError
        is_mainnet = entry.data["backend"] == "embedded_mainnet"
        module = await async_import_module(
            hass, f"custom_components.{DOMAIN}.{'mainnet' if is_mainnet else 'embedded'}")
        api = (module.MainnetWalletAPI if is_mainnet else module.EmbeddedWalletAPI)(hass, entry)
        try:
            await api.load()
        except WalletError as exc:
            raise ConfigEntryNotReady(str(exc)) from None
    else:
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
