"""UI setup. No approval credential is ever stored in HA."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .api import WalletAPI, WalletError, validate_url
from .const import DOMAIN


class BSVSettlementConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                url = validate_url(user_input["service_url"])
                api = WalletAPI(async_get_clientsession(self.hass), url, user_input["api_token"])
                await api.call("GET", "/v1/health")
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="BSV Settlement (Mock)",
                                               data={**user_input, "service_url": url})
            except (WalletError, ValueError):
                errors["base"] = "cannot_connect"
        schema = vol.Schema({
            vol.Required("service_url", default="http://127.0.0.1:8091"): str,
            vol.Required("api_token"): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
