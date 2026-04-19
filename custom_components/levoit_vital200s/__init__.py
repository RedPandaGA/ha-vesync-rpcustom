"""Levoit Vital 200S Air Purifier Integration."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_PASSWORD,
    CONF_TIME_ZONE,
    CONF_USERNAME,
    DEFAULT_TIME_ZONE,
    DOMAIN,
    SCAN_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["fan", "sensor", "switch", "select"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Levoit Vital 200S from a config entry."""
    from pyvesync import VeSync

    manager = VeSync(
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        time_zone=entry.data.get(CONF_TIME_ZONE, DEFAULT_TIME_ZONE),
    )

    try:
        await manager.__aenter__()
        await manager.login()
        if not manager.enabled:
            await manager.__aexit__(None, None, None)
            raise ConfigEntryNotReady("Failed to log in to VeSync")
        await manager.get_devices()
        await manager.update()
    except ConfigEntryNotReady:
        raise
    except Exception as err:
        try:
            await manager.__aexit__(None, None, None)
        except Exception:
            pass
        raise ConfigEntryNotReady(f"Error connecting to VeSync: {err}") from err

    devices = [
        d for d in manager.devices.air_purifiers
        if "V201S" in d.device_type
    ]

    if not devices:
        await manager.__aexit__(None, None, None)
        raise ConfigEntryNotReady("No Levoit Vital 200S devices found")

    async def async_update_data() -> dict:
        """Fetch latest state from VeSync."""
        try:
            await manager.update()
        except Exception as err:
            raise UpdateFailed(f"Error updating VeSync: {err}") from err
        return {
            d.cid: d
            for d in manager.devices.air_purifiers
            if "V201S" in d.device_type
        }

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "manager": manager,
        "coordinator": coordinator,
        "devices": devices,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        try:
            await data["manager"].__aexit__(None, None, None)
        except Exception:
            pass
    return unload_ok
