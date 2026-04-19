"""Config flow for Levoit Vital 200S integration."""

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_PASSWORD,
    CONF_TIME_ZONE,
    CONF_USERNAME,
    DEFAULT_TIME_ZONE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_TIME_ZONE, default=DEFAULT_TIME_ZONE): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate credentials and check at least one V201S exists."""
    from pyvesync import VeSync

    manager = VeSync(
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        time_zone=data.get(CONF_TIME_ZONE, DEFAULT_TIME_ZONE),
    )

    try:
        async with manager:
            await manager.login()
            if not manager.enabled:
                raise InvalidAuth("Login failed — check email/password")
            await manager.get_devices()
            devices = [
                d for d in manager.devices.air_purifiers
                if "V201S" in d.device_type
            ]
            if not devices:
                raise NoDevicesFound("No V201S devices on this account")
    except (InvalidAuth, NoDevicesFound):
        raise
    except Exception as err:
        _LOGGER.exception("Unexpected error during login")
        raise CannotConnect from err

    return {"title": f"Levoit Vital 200S ({data[CONF_USERNAME]})"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Levoit Vital 200S."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except NoDevicesFound:
                errors["base"] = "no_devices"
            except Exception:
                _LOGGER.exception("Unexpected exception in config flow")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Cannot connect to VeSync."""


class InvalidAuth(HomeAssistantError):
    """Invalid credentials."""


class NoDevicesFound(HomeAssistantError):
    """No V201S devices found on account."""
