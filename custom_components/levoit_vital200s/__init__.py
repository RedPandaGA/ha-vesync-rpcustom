"""Levoit Vital 200S Air Purifier Integration."""

import asyncio
import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_call_later
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

# Delays (seconds) for the two follow-up polls after a command
BURST_DELAYS = (1.0, 4.0)


# How long (seconds) to hold optimistic state before trusting cloud polls again.
# VeSync cloud can take 10-20s to reflect a command the device already acted on.
OPTIMISTIC_HOLD_SECONDS = 180


class LevoitCoordinator(DataUpdateCoordinator):
    """Coordinator with optimistic command state and per-device polling.

    The VeSync cloud can lag 10-20 seconds behind the physical device after a
    command — it acknowledges the command (code 0) but continues returning the
    old state in subsequent getPurifierStatus polls.  Meanwhile the device
    itself responds instantly.

    Strategy:
    - Commands apply an optimistic state snapshot immediately (the entity does
      this via async_write_ha_state before calling us).
    - We record that snapshot here with a timestamp so that poll results for
      the same device are silently ignored during the hold window.
    - After OPTIMISTIC_HOLD_SECONDS the hold expires and polls are trusted again.
    - Burst polls still run so we catch the cloud eventually syncing, but they
      don't stomp the optimistic state while the hold is active.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._burst_active: bool = False
        self._burst_cancel_callbacks: list = []
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
        """Schedule follow-up polls after a command to catch cloud sync.

        Polls still run so we eventually confirm the cloud caught up, but
        apply_optimistic_hold() will suppress any conflicting values until
        the hold window expires.

        Safe to call from any async context — does not await anything.
        """
        if self._burst_active:
            _LOGGER.debug(
                "Burst already active, skipping new burst (device: %s)",
                device.device_name,
            )
            return

        self._burst_active = True
        _LOGGER.debug(
            "Scheduling burst polls for '%s' at %s s",
            device.device_name,
            BURST_DELAYS,
        )

        num_polls = len(BURST_DELAYS)

        def _make_callback(delay_index: int):
            async def _do_poll(_now) -> None:
                is_last = delay_index == num_polls - 1
                try:
                    _LOGGER.debug(
                        "Burst poll %d/%d: calling get_details() for '%s'",
                        delay_index + 1,
                        num_polls,
                        device.device_name,
                    )
                    from pyvesync.utils.helpers import Helpers
                    Helpers.get_defaultvalues_attributes.cache_clear()
                    await device.get_details()
                    if self.data and device.cid in self.data:
                        self.data[device.cid] = device
                    self.async_set_updated_data(self.data)
                    _LOGGER.debug(
                        "Burst poll %d/%d complete for '%s': mode=%s speed=%s",
                        delay_index + 1,
                        num_polls,
                        device.device_name,
                        device.state.mode,
                        device.state.fan_set_level,
                    )
                except Exception as err:
                    _LOGGER.debug(
                        "Burst poll %d/%d failed for '%s': %s",
                        delay_index + 1,
                        num_polls,
                        device.device_name,
                        err,
                    )
                finally:
                    if is_last:
                        self._burst_active = False
                        self._burst_cancel_callbacks.clear()
                        _LOGGER.debug(
                            "Burst complete for '%s'",
                            device.device_name,
                        )

            return _do_poll

        for i, delay in enumerate(BURST_DELAYS):
            cancel = async_call_later(self.hass, delay, _make_callback(i))
            self._burst_cancel_callbacks.append(cancel)

    def cancel_burst(self) -> None:
        """Cancel any pending burst callbacks (called on unload)."""
        for cancel in self._burst_cancel_callbacks:
            try:
                cancel()
            except Exception:
                pass
        self._burst_cancel_callbacks.clear()
        self._burst_active = False


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
