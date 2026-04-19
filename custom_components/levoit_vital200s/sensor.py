"""Sensor platform for Levoit Vital 200S."""

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Map numeric AQ level to human-readable string
AIR_QUALITY_MAP = {
    1: "excellent",
    2: "good",
    3: "moderate",
    4: "poor",
    5: "very_poor",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Levoit Vital 200S sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    devices = data["devices"]

    entities = []
    for device in devices:
        entities.extend([
            LevoitAirQualitySensor(coordinator, device),
            LevoitPM25Sensor(coordinator, device),
            LevoitFilterLifeSensor(coordinator, device),
        ])

    async_add_entities(entities)


class LevoitBaseSensor(CoordinatorEntity, SensorEntity):
    """Shared base for all Levoit sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device) -> None:
        """Initialize base sensor."""
        super().__init__(coordinator)
        self._device = device
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


class LevoitAirQualitySensor(LevoitBaseSensor):
    """Air quality level as a text category (excellent → very_poor)."""

    _attr_name = "Air Quality"
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator, device) -> None:
        """Initialize air quality sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.cid}_air_quality"

    @property
    def native_value(self) -> str | None:
        """Return human-readable air quality level."""
        raw = self._device.state.air_quality_level
        if raw is None:
            return None
        try:
            return AIR_QUALITY_MAP.get(int(raw), str(raw))
        except (ValueError, TypeError):
            return str(raw)

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the raw numeric level alongside the text value."""
        return {"raw_level": self._device.state.air_quality_level}


class LevoitPM25Sensor(LevoitBaseSensor):
    """PM2.5 particulate concentration sensor."""

    _attr_name = "PM2.5"
    _attr_device_class = SensorDeviceClass.PM25
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = CONCENTRATION_MICROGRAMS_PER_CUBIC_METER

    def __init__(self, coordinator, device) -> None:
        """Initialize PM2.5 sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.cid}_pm25"

    @property
    def native_value(self) -> int | None:
        """Return PM2.5 value in µg/m³."""
        return self._device.state.pm25


class LevoitFilterLifeSensor(LevoitBaseSensor):
    """Remaining filter life as a percentage."""

    _attr_name = "Filter Life"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator, device) -> None:
        """Initialize filter life sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.cid}_filter_life"

    @property
    def native_value(self) -> int | None:
        """Return filter life percentage."""
        return self._device.state.filter_life
