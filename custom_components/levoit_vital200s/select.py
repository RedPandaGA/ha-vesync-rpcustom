"""Select platform for Levoit Vital 200S (auto mode preference)."""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import AUTO_PREFERENCES, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the auto preference select entity."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    devices = data["devices"]

    async_add_entities(
        [LevoitAutoPreferenceSelect(coordinator, device) for device in devices]
    )


class LevoitAutoPreferenceSelect(CoordinatorEntity, SelectEntity):
    """Select entity for auto mode preference (default / efficient / quiet)."""

    _attr_has_entity_name = True
    _attr_name = "Auto Preference"
    _attr_icon = "mdi:tune"
    _attr_options = AUTO_PREFERENCES

    def __init__(self, coordinator, device) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.cid}_auto_preference"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.cid)},
            "name": device.device_name,
            "manufacturer": "Levoit",
            "model": device.device_type,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh device reference and push new state."""
        if self.coordinator.data and self._device.cid in self.coordinator.data:
            self._device = self.coordinator.data[self._device.cid]
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if the device is online."""
        return (
            self.coordinator.last_update_success
            and str(self._device.state.connection_status) == "online"
        )

    @property
    def current_option(self) -> str | None:
        """Return the currently selected auto preference."""
        return self._device.state.auto_preference_type

    async def async_select_option(self, option: str) -> None:
        """Send the selected auto preference to the device."""
        if option not in AUTO_PREFERENCES:
            _LOGGER.error("Invalid auto preference: %s", option)
            return
        await self._device.set_auto_preference(option)
        self.coordinator.async_burst_refresh(self._device)
