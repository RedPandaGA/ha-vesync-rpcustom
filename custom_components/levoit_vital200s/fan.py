"""Fan platform for Levoit Vital 200S."""

import logging
import math
import time

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.percentage import (
    int_states_in_range,
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from .const import (
    DOMAIN,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_PET,
    MODE_SLEEP,
    PRESET_MODES,
    SPEED_RANGE,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Levoit Vital 200S fan platform."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    devices = data["devices"]

    async_add_entities(
        [LevoitVital200SFan(coordinator, device) for device in devices],
        update_before_add=False,
    )


class LevoitVital200SFan(CoordinatorEntity, FanEntity):
    """Representation of a Levoit Vital 200S air purifier."""

    _attr_has_entity_name = True
    _attr_name = None  # Use device name as the entity name
    _attr_preset_modes = PRESET_MODES
    _attr_supported_features = (
        FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
    )

    def __init__(self, coordinator, device) -> None:
        """Initialize the fan entity."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.cid}_fan"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.cid)},
            "name": device.device_name,
            "manufacturer": "Levoit",
            "model": device.device_type,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update device reference from coordinator data and refresh state.

        If an optimistic hold is active (command was sent but cloud hasn't
        synced yet), re-apply the known-good state over whatever the poll
        returned so the UI doesn't flicker back to the old value.
        """
        if self.coordinator.data and self._device.cid in self.coordinator.data:
            self._device = self.coordinator.data[self._device.cid]
        # Re-apply optimistic state if cloud is still lagging
        self.coordinator.apply_optimistic_hold(self._device)
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
        """Return true if the fan is on."""
        return self._device.is_on

    @property
    def percentage(self) -> int | None:
        """Return the current speed as a percentage.

        Only meaningful in manual mode. Uses fan_set_level (the commanded
        speed) which stays stable even when fan_level temporarily reports 0.
        Falls back to fan_level if fan_set_level is not set.
        """
        if self._device.state.mode != MODE_MANUAL:
            return None
        level = self._device.state.fan_set_level or self._device.state.fan_level
        if not level:
            return None
        return ranged_value_to_percentage(SPEED_RANGE, level)

    @property
    def speed_count(self) -> int:
        """Return the number of discrete speed steps."""
        return int_states_in_range(SPEED_RANGE)

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        mode = self._device.state.mode
        return mode if mode in PRESET_MODES else None

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra attributes for display in the UI.

        Poll timing attributes are included so dashboards can display a live
        countdown to the next cloud sync without needing their own timers:

          poll_interval_seconds   — configured polling interval (const)
          seconds_since_last_poll — how long ago the last poll completed
          seconds_until_next_poll — estimated seconds until the next poll
          optimistic_hold_active  — True while local state overrides cloud data
          optimistic_hold_expires — monotonic timestamp when hold expires (or 0)
        """
        from .const import SCAN_INTERVAL_SECONDS
        s = self._device.state
        now = time.monotonic()

        last_poll = self.coordinator.last_poll_monotonic
        since_last = round(now - last_poll) if last_poll else None
        until_next = (
            max(0, round(SCAN_INTERVAL_SECONDS - (now - last_poll)))
            if last_poll else None
        )

        hold = self.coordinator._optimistic_holds.get(self._device.cid)
        hold_active = bool(hold and now < hold["until"])
        hold_expires_in = (
            max(0, round(hold["until"] - now)) if hold_active else 0
        )

        return {
            "filter_life": s.filter_life,
            "air_quality_level": s.air_quality_level,
            "pm25": s.pm25,
            "child_lock": s.child_lock,
            "display": str(s.display_set_status),
            "auto_preference": s.auto_preference_type,
            "light_detection": str(s.light_detection_switch),
            "fan_level": s.fan_level,
            "fan_set_level": s.fan_set_level,
            "mode": s.mode,
            # Poll timing — readable by Lovelace cards / template sensors
            "poll_interval_seconds": SCAN_INTERVAL_SECONDS,
            "seconds_since_last_poll": since_last,
            "seconds_until_next_poll": until_next,
            "optimistic_hold_active": hold_active,
            "optimistic_hold_expires_in": hold_expires_in,
        }

    async def async_set_percentage(self, percentage: int) -> None:
        """Set fan speed from a percentage value."""
        if percentage == 0:
            await self._device.turn_off()
            self._device.state.device_status = "off"
            self.async_write_ha_state()
            self.coordinator.set_optimistic_hold(self._device, {"device_status": "off"})
            return

        if not self.is_on:
            await self._device.turn_on()

        level = math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))
        level = max(SPEED_RANGE[0], min(SPEED_RANGE[1], level))

        await self._device.set_fan_speed(level)

        # Optimistic update — write local state immediately
        self._device.state.mode = MODE_MANUAL
        self._device.state.fan_set_level = level
        self._device.state.fan_level = level
        self.async_write_ha_state()

        # Hold this state for 20s so cloud lag doesn't flip the UI back
        self.coordinator.set_optimistic_hold(
            self._device,
            {"mode": MODE_MANUAL, "fan_set_level": level, "fan_level": level},
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode."""
        if preset_mode not in PRESET_MODES:
            _LOGGER.error("Invalid preset mode: %s", preset_mode)
            return

        if not self.is_on:
            await self._device.turn_on()

        if preset_mode == MODE_AUTO:
            await self._device.set_auto_mode()
        elif preset_mode == MODE_SLEEP:
            await self._device.set_sleep_mode()
        elif preset_mode == MODE_PET:
            await self._device.set_pet_mode()
        elif preset_mode == MODE_MANUAL:
            await self._device.set_manual_mode()

        # Optimistic update
        self._device.state.mode = preset_mode
        self.async_write_ha_state()

        # Hold this state so cloud lag doesn't flip the UI back
        self.coordinator.set_optimistic_hold(
            self._device,
            {"mode": preset_mode},
        )

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs,
    ) -> None:
        """Turn the fan on, optionally setting speed or mode."""
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
            return
        if percentage is not None:
            await self.async_set_percentage(percentage)
            return
        await self._device.turn_on()
        self._device.state.device_status = "on"
        self.async_write_ha_state()
        self.coordinator.set_optimistic_hold(self._device, {"device_status": "on"})

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the fan off."""
        await self._device.turn_off()
        self._device.state.device_status = "off"
        self.async_write_ha_state()
        self.coordinator.set_optimistic_hold(self._device, {"device_status": "off"})
