"""Levoit Vital 200S Air Purifier Integration."""

import asyncio
import logging
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


class LevoitCoordinator(DataUpdateCoordinator):
    """Coordinator with per-device burst-polling after commands.

    The VeSync API reuses the same traceId across all calls in a single
    manager.update() cycle, which causes the server to return a cached/wrong
    response when two devices are polled back-to-back.  Calling
    device.get_details() directly on one device issues its own independent
    request with a fresh traceId, so the response is always correct.

    After a command, the entity calls async_burst_refresh(device).  This
    schedules get_details() on just that device at +1 s and +4 s, then
    notifies all listeners so the UI updates immediately.  The normal 30 s
    full poll continues unaffected.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._burst_active: bool = False
        self._burst_cancel_callbacks: list = []

    def async_burst_refresh(self, device) -> None:
        """Schedule rapid per-device polls after a command.

        Calls device.get_details() independently — avoids the shared-traceId
        bug in manager.update() that returns stale data for the second device.

        Safe to call from any async context — does not await anything.
        """
        if self._burst_active:
            _LOGGER.debug(
                "Burst already active for another command, skipping (device: %s)",
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
                    await device.get_details()
                    # Update coordinator data in-place and notify all listeners
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
                            "Burst complete for '%s', resuming normal interval",
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
