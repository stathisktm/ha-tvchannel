"""Config flow for TV Channel Watcher."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .const import (
    ANDROIDTV_ADBKEY_DIR,
    ANDROIDTV_ADBKEY_FILENAME,
    CONF_ADBKEY_PATH,
    CONF_HOST,
    CONF_MEDIA_PLAYER,
    CONF_MODE,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MODE_LOGCAT_STREAM,
    MODE_SETTINGS_KEY,
    MODES,
)


def _schema(hass: HomeAssistant, defaults: dict[str, Any]) -> vol.Schema:
    default_adbkey_path = hass.config.path(ANDROIDTV_ADBKEY_DIR, ANDROIDTV_ADBKEY_FILENAME)
    return vol.Schema(
        {
            vol.Required(CONF_MEDIA_PLAYER, default=defaults.get(CONF_MEDIA_PLAYER)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player"),
            ),
            vol.Required(CONF_MODE, default=defaults.get(CONF_MODE, MODE_SETTINGS_KEY)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=MODES,
                    translation_key=CONF_MODE,
                ),
            ),
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
            vol.Optional(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Optional(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535),
            ),
            vol.Optional(
                CONF_ADBKEY_PATH,
                default=defaults.get(CONF_ADBKEY_PATH, default_adbkey_path),
            ): str,
        },
    )


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    """host is only meaningful (and only actually required) for MODE_LOGCAT_STREAM, which opens
    its own direct TCP connection to the TV instead of going through the androidtv integration's
    existing one — scan_interval is likewise meaningless there (nothing to poll on a timer)."""
    errors: dict[str, str] = {}
    if user_input.get(CONF_MODE) == MODE_LOGCAT_STREAM and not user_input.get(CONF_HOST, "").strip():
        errors[CONF_HOST] = "host_required_for_logcat_stream"
    return errors


class TvChannelConfigFlow(ConfigFlow, domain=DOMAIN):
    """One TV per config entry — the adb-capable media_player entity to poll/watch."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                await self.async_set_unique_id(user_input[CONF_MEDIA_PLAYER])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=user_input[CONF_MEDIA_PLAYER], data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema(self.hass, user_input or {}), errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TvChannelOptionsFlow()


class TvChannelOptionsFlow(OptionsFlow):
    """Lets the target entity, mode, or connection details be changed later without a full re-add."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(title="", data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options, **(user_input or {})}
        return self.async_show_form(step_id="init", data_schema=_schema(self.hass, current), errors=errors)
