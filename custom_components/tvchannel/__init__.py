"""TV Channel Watcher — tracks which live-TV channel is currently tuned on an Android TV, without
querying the TV's own channel database directly (that needs a signature-level EPG permission that
isn't obtainable without root on this hardware — confirmed, on two different TCL firmware builds).

Two independent modes, picked per TV in the config flow, since not every TV exposes the same thing:

- MODE_SETTINGS_KEY: periodically reads TCL's own `recent_play_channel_id` system setting via the
  already-configured `androidtv` integration's `adb_command` service — a single tiny `settings get`
  shell command each poll, deliberately infrequent by default so it never meaningfully loads the TV.
  Works on firmware that actually populates this setting (confirmed on one TV; confirmed absent
  entirely — not merely empty — on another, different TCL firmware branch).

- MODE_LOGCAT_STREAM: for a TV whose firmware has no such setting at all, and whose channel state
  turned out (traced through its own compiled AIDL service) to be a pure push/callback design with
  no synchronous "what's on right now" call anywhere in the framework. The live-TV app logs the
  channel name at debug level on every real channel change, but polling for it misses updates once
  the device's own very high log volume rotates the line out of the buffer (confirmed: well under
  10 minutes). A continuously open `logcat` stream never has that problem — it sees every line as
  it's written, with nothing to poll or miss — so this mode opens its own persistent direct ADB TCP
  connection (not routed through the androidtv integration, which is designed for one-shot
  request/response commands, not an unbounded stream) and reconnects automatically if it drops.

Either way, all this integration ever exposes is a raw value (a numeric id, or a channel name
string) — turning that into whatever the user actually wants shown is left entirely to their own
template sensor, grown at their own pace as they learn the local channel lineup, with no need to
touch or reinstall this integration to add one.
"""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ADB_COMMAND,
    CONF_MEDIA_PLAYER,
    CONF_MODE,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MODE_LOGCAT_STREAM,
    MODE_SETTINGS_KEY,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


def get_config(entry: ConfigEntry, key: str, default=None):
    """A value set later via the options flow (the "Configure" button on an existing entry) lands
    in entry.options, completely separate from entry.data (the original setup values) — reading
    only entry.data, which every config value in this integration used to do, meant re-configuring
    an entry (e.g. switching mode) silently had NO effect at all: the old data values kept being
    used forever. This is the one place every config value is read, everywhere, specifically so
    that can't happen again."""
    return entry.options.get(key, entry.data.get(key, default))


class TvChannelCoordinator(DataUpdateCoordinator[str | None]):
    """Polls one TV's current channel id, gently — see module docstring (MODE_SETTINGS_KEY)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.media_player_entity_id: str = get_config(entry, CONF_MEDIA_PLAYER)
        interval = get_config(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.media_player_entity_id}",
            update_interval=timedelta(seconds=interval),
        )

    async def _async_update_data(self) -> str | None:
        try:
            await self.hass.services.async_call(
                "androidtv",
                "adb_command",
                {ATTR_ENTITY_ID: self.media_player_entity_id, "command": ADB_COMMAND},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001 - surfaced as a coordinator update failure either way
            raise UpdateFailed(f"adb_command failed: {err}") from err

        # androidtv.adb_command stores the shell command's stdout as this attribute on the target
        # media_player entity itself — there's no separate response object to await.
        state = self.hass.states.get(self.media_player_entity_id)
        response = state.attributes.get("adb_response") if state else None
        return response.strip() if isinstance(response, str) else None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    mode = get_config(entry, CONF_MODE, MODE_SETTINGS_KEY)
    if mode == MODE_SETTINGS_KEY:
        coordinator = TvChannelCoordinator(hass, entry)
        await coordinator.async_config_entry_first_refresh()
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    else:
        # MODE_LOGCAT_STREAM has no coordinator at all — sensor.py builds a self-contained entity
        # that manages its own persistent connection and pushes its own state updates as they
        # arrive, which is what a push/streaming source actually calls for instead of a poll timer.
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = None

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # The options flow may have changed the mode, scan interval, target entity, or host/port —
    # simplest correct fix is to fully re-set-up the entry rather than try to patch a live
    # coordinator/connection in place.
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
