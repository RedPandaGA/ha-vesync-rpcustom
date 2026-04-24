"""Levoit Vital 200S Air Purifier Integration."""

import asyncio
import logging
import time
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



# How long (seconds) to hold optimistic state before trusting cloud polls again.
# VeSync cloud can take 10-20s to reflect a command the device already acted on.
OPTIMISTIC_HOLD_SECONDS = 180


class LevoitCoordinator(DataUpdateCoordinator):
    """Coordinator with optimistic command state and per-device polling.

    The VeSync cloud can take up to 3 minutes to reflect a command even though
    the physical device responds instantly.  To keep the UI accurate during
    that window, commands write an optimistic state snapshot here.  Subsequent
    poll results for that device are silently overridden until the hold expires
    (OPTIMISTIC_HOLD_SECONDS), after which cloud state is trusted again.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # cid -> {"until": float, "state": dict}
        self._optimistic_holds: dict = {}
        # Monotonic timestamp of the last completed successful poll
        self.last_poll_monotonic: float = 0.0

    def set_optimistic_hold(self, device, state_snapshot: dict) -> None:
        """Record expected state for a device and suppress conflicting polls.

        Args:
            device: The pyvesync device that received a command.
            state_snapshot: Dict of state field names -> expected values,
                e.g. {"mode": "manual", "fan_set_level": 4, "fan_level": 4}.
        """
        until = time.monotonic() + OPTIMISTIC_HOLD_SECONDS
        self._optimistic_holds[device.cid] = {"until": until, "state": state_snapshot}
        _LOGGER.debug(
            "Optimistic hold set for '%s' for %ds: %s",
            device.device_name,
            OPTIMISTIC_HOLD_SECONDS,
            state_snapshot,
        )

    def apply_optimistic_hold(self, device) -> bool:
        """Re-apply optimistic state if the hold window is still active.

        Called by _handle_coordinator_update in each entity after a poll
        updates device.state. If the hold is active, we overwrite whatever
        the poll returned with the known-good optimistic values so the UI
        doesn't flicker back.

        Returns True if the hold was applied (caller should write HA state),
        False if the hold has expired and poll data should be trusted.
        """
        hold = self._optimistic_holds.get(device.cid)
        if not hold:
            return False
        if time.monotonic() > hold["until"]:
            del self._optimistic_holds[device.cid]
            _LOGGER.debug(
                "Optimistic hold expired for '%s', trusting cloud state",
                device.device_name,
            )
            return False
        # Re-stamp the optimistic values onto the device state object
        for field, value in hold["state"].items():
            try:
                setattr(device.state, field, value)
            except Exception:
                pass
        _LOGGER.debug(
            "Optimistic hold active for '%s', overriding poll result: %s",
            device.device_name,
            hold["state"],
        )
        return True

    def async_burst_refresh(self, device) -> None:
        """Burst polling disabled — optimistic hold covers cloud lag instead.

        The VeSync cloud can take up to 3 minutes to reflect a command, so
        rapid follow-up polls provided no benefit and just generated noise.
        The optimistic hold system keeps the UI accurate during that window.
        """

    def cancel_burst(self) -> None:
        """No-op — burst polling is disabled."""


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
        from pyvesync.utils.helpers import Helpers
        for d in manager.devices.air_purifiers:
            if "V201S" in d.device_type:
                Helpers.get_defaultvalues_attributes.cache_clear()
                await d.get_details()
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
        """Fetch latest state by calling get_details() on each device individually.

        Deliberately avoids manager.update() which runs all get_details() calls
        concurrently under a shared traceId. The VeSync API caches responses by
        traceId and returns stale data to the second device in the batch.
        Sequential individual calls each get their own fresh traceId.

        pyvesync uses @lru_cache on get_defaultvalues_attributes(), which freezes
        the traceId on first call. We bust the cache before each device poll so
        DefaultValues.traceId() is re-evaluated and produces a unique value.
        """
        from pyvesync.utils.helpers import Helpers
        result = {}
        for d in list(manager.devices.air_purifiers):
            if "V201S" not in d.device_type:
                continue
            try:
                # Bust the lru_cache so traceId is re-evaluated for this request
                Helpers.get_defaultvalues_attributes.cache_clear()
                await d.get_details()
                result[d.cid] = d
            except Exception as err:
                _LOGGER.warning(
                    "Failed to update device '%s': %s", d.device_name, err
                )
        if not result:
            raise UpdateFailed("All device updates failed")
        coordinator.last_poll_monotonic = time.monotonic()
        return result

    coordinator = LevoitCoordinator(
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
        data["coordinator"].cancel_burst()
        try:
            await data["manager"].__aexit__(None, None, None)
        except Exception:
            pass
    return unload_ok
