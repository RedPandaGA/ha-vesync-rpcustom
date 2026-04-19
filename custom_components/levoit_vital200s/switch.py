"""Switch platform for Levoit Vital 200S (display, child lock, light detection)."""

import logging
from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class SwitchDef:
    """Definition of a toggleable switch entity."""
    key: str
    name: str
    icon: str
    # Lambda receives the device and returns current bool state
    get_state: object
    # Lambda receives (device, bool) and awaits the appropriate method
    set_state: object


SWITCH_DEFS = [
    SwitchDef(
        key="display",
        name="Display",
        icon="mdi:monitor",
        get_state=lambda d: str(d.state.display_set_status) == "on",
        set_state=lambda d, v: d.toggle_display(v),
    ),
    SwitchDef(
        key="child_lock",
        name="Child Lock",
        icon="mdi:lock",
        get_state=lambda d: bool(d.state.child_lock),
        set_state=lambda d, v: d.toggle_child_lock(v),
    ),
    SwitchDef(
        key="light_detection",
        name="Light Detection",
        icon="mdi:brightness-auto",
        get_state=lambda d: str(d.state.light_detection_switch) == "on",
        set_state=lambda d, v: d.toggle_light_detection(v),
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Levoit Vital 200S switches."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    devices = data["devices"]

    entities = [
        LevoitSwitch(coordinator, device, switch_def)
        for device in devices
        for switch_def in SWITCH_DEFS
    ]
    async_add_entities(entities)


class LevoitSwitch(CoordinatorEntity, SwitchEntity):
    """A toggleable switch entity for the Vital 200S."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device, switch_def: SwitchDef) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device = device
        self._def = switch_def
        self._attr_unique_id = f"{device.cid}_{switch_def.key}"
        self._attr_name = switch_def.name
        self._attr_icon = switch_def.icon
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
    def is_on(self) -> bool:
        """Return current switch state."""
        try:
            return self._def.get_state(self._device)
        except Exception:
            return False

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the switch on."""
        await self._def.set_state(self._device, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the switch off."""
        await self._def.set_state(self._device, False)
        await self.coordinator.async_request_refresh()
