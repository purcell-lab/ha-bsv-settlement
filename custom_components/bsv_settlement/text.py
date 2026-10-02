"""Native HA more-info input dialogs for public driver details only."""
from homeassistant.components.text import TextEntity, TextMode
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import WalletError
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.mode != "embedded_mainnet":
        return
    async_add_entities([
        DriverText(coordinator, entry, "driver_public_identity", "Driver public identity", 130),
        DriverText(coordinator, entry, "driver_receive_address", "Driver receiving address", 34),
    ])


class DriverText(CoordinatorEntity, TextEntity):
    _attr_has_entity_name = True
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_icon = "mdi:account-key-outline"

    def __init__(self, coordinator, entry, field, name, maximum):
        super().__init__(coordinator)
        self.field = field
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{field}"
        self._attr_native_max = maximum
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    @property
    def native_value(self):
        return self.coordinator.api.saved["driver"][self.field]

    async def async_set_value(self, value):
        try:
            async with self.coordinator.lock:
                await self.coordinator.api.set_driver(self.field, value)
            await self.coordinator.async_request_refresh()
        except WalletError as exc:
            raise HomeAssistantError(str(exc)) from None
