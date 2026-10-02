"""Choose the existing mock service or an unfunded embedded testnet wallet."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .api import WalletAPI, WalletError, validate_url
from .const import DOMAIN


class BSVSettlementConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            if user_input["backend"] == "embedded_testnet":
                return await self.async_step_embedded()
            return await self.async_step_mock()
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("backend", default="mock"): vol.In(["mock", "embedded_testnet"]),
        }))

    async def async_step_embedded(self, user_input=None):
        errors = {}
        if user_input is not None:
            if user_input.get("acknowledge_key_custody") is not True:
                errors["base"] = "acknowledgement_required"
            else:
                await self.async_set_unique_id("embedded-operator-testnet")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="BSV Operator Wallet (Testnet, Broadcast Disabled)",
                    data={"backend": "embedded_testnet", "network": "testnet",
                          "acknowledge_key_custody": True})
        return self.async_show_form(step_id="embedded", data_schema=vol.Schema({
            vol.Required("acknowledge_key_custody", default=False): bool,
        }), errors=errors)

    async def async_step_mock(self, user_input=None):
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
        return self.async_show_form(step_id="mock", data_schema=schema, errors=errors)
