"""The one entity this integration exposes per mode: the TV's current channel, raw (an id in
MODE_SETTINGS_KEY, a channel name in MODE_LOGCAT_STREAM — see the package docstring for why they
differ). Deliberately not a friendly mapped name in either case: the user's own template sensor is
what turns this into one, at their own pace, without ever needing to touch this integration."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
import logging
import re

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import TvChannelCoordinator, get_config
from .const import (
    ADB_COMMAND,
    ANDROIDTV_ADBKEY_DIR,
    ANDROIDTV_ADBKEY_FILENAME,
    CONF_ADBKEY_PATH,
    CONF_HOST,
    CONF_MEDIA_PLAYER,
    CONF_MODE,
    CONF_PORT,
    CURRENT_PROGRAM_PATTERN,
    DEFAULT_PORT,
    DOMAIN,
    LOGCAT_COMMAND,
    MODE_LOGCAT_STREAM,
    CHANNEL_ID_POLL_S,
    PROGRAM_NAME_PATTERN,
    RECONNECT_DELAY_S,
)

_LOGGER = logging.getLogger(__name__)

_PROGRAM_NAME_RE = re.compile(PROGRAM_NAME_PATTERN)
_CURRENT_PROGRAM_RE = re.compile(CURRENT_PROGRAM_PATTERN)

# Generous on purpose — nothing is wrong with the connection just because nobody changed the
# channel for a while; this only bounds how long a single streaming_shell() read can block before
# the library itself gives up and we go through the (harmless, self-healing) reconnect path.
_READ_TIMEOUT_S = 3600.0


def _load_or_generate_key(path: str):
    """Runs in an executor — file I/O and RSA key generation, neither belongs on the event loop.
    Generates a new key pair at [path] only if one doesn't already exist there; loading an
    existing one (the expected, common case: the androidtv integration's own key, already
    authorized on the TV) never touches the TV or needs any new on-screen confirmation."""
    import os

    from adb_shell.auth.keygen import keygen
    from adb_shell.auth.sign_pythonrsa import PythonRSASigner

    if not os.path.isfile(path):
        keygen(path)
    with open(path, encoding="utf-8") as f:
        private_key = f.read()
    with open(f"{path}.pub", encoding="utf-8") as f:
        public_key = f.read()
    return PythonRSASigner(public_key, private_key)


def _device_info(entry: ConfigEntry, label: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"TV Channel Watcher ({label})",
        manufacturer="pestpoint",
        model="tvchannel",
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    if get_config(entry, CONF_MODE) == MODE_LOGCAT_STREAM:
        channel_sensor = TvChannelLogcatSensor(entry)
        program_sensor = TvChannelProgramSensor(entry)
        # Fed from inside channel_sensor's own read loop (see TvChannelLogcatSensor._run) rather
        # than opening a second connection — purely additive, never touches the channel-name
        # matching that loop already does.
        channel_sensor.attach_program_sensor(program_sensor)
        async_add_entities([channel_sensor, program_sensor])
    else:
        coordinator: TvChannelCoordinator = hass.data[DOMAIN][entry.entry_id]
        async_add_entities([TvChannelIdSensor(coordinator, entry)])


class TvChannelIdSensor(CoordinatorEntity[TvChannelCoordinator], SensorEntity):
    """MODE_SETTINGS_KEY — raw channel id, polled gently."""

    _attr_icon = "mdi:television-classic"
    _attr_has_entity_name = True
    _attr_translation_key = "channel_id"

    def __init__(self, coordinator: TvChannelCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_channel_id"
        self._attr_device_info = _device_info(entry, coordinator.media_player_entity_id)

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            "target_entity": self.coordinator.media_player_entity_id,
            "adb_command": ADB_COMMAND,
        }


class TvChannelLogcatSensor(SensorEntity, RestoreEntity):
    """MODE_LOGCAT_STREAM — raw channel NAME (not an id — there's no id to have here), pushed live
    from a continuously open `logcat` stream over its own direct ADB TCP connection, instead of
    polled. See the package docstring for why a poll can't work for this particular firmware.

    Self-healing: any disconnect (TV reboot, Wi-Fi blip, ...) is caught, logged, and retried after
    RECONNECT_DELAY_S — indefinitely, for as long as this entity is loaded — rather than ever
    surfacing as a failed/one-shot update the way a coordinator-based entity would.
    """

    _attr_icon = "mdi:television-classic"
    _attr_has_entity_name = True
    _attr_translation_key = "channel_name"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._host: str = get_config(entry, CONF_HOST)
        self._port: int = get_config(entry, CONF_PORT, DEFAULT_PORT)
        self._adbkey_path: str = get_config(entry, CONF_ADBKEY_PATH) or ""
        self._attr_unique_id = f"{entry.entry_id}_channel_name"
        self._attr_device_info = _device_info(entry, get_config(entry, CONF_MEDIA_PLAYER))
        self._attr_available = False
        self._attr_native_value: str | None = None
        self._task: asyncio.Task | None = None
        self._program_sensor: TvChannelProgramSensor | None = None
        self._media_player: str = get_config(entry, CONF_MEDIA_PLAYER)
        # The log only names the channel at the moment it changes, so on its own this lost the
        # channel after every HA restart (and after the TV came back from standby) until the next
        # change. Two fixes: the last value is restored across restarts, and on TVs whose firmware
        # keeps `recent_play_channel_id` the current id is read once a minute (one tiny `settings
        # get`, nothing on screen) and turned into a name through an id -> name map this sensor
        # learns by itself, every time the log names a channel.
        self._channel_id: str | None = None
        self._id_names: dict[str, str] = {}
        self._id_supported: bool | None = None  # None = not probed yet
        self._store: Store[dict[str, str]] | None = None

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"channel_id": self._channel_id}

    def attach_program_sensor(self, program_sensor: "TvChannelProgramSensor") -> None:
        """Optional sibling entity fed from this one's own read loop — see async_setup_entry."""
        self._program_sensor = program_sensor

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unknown", "unavailable"):
            self._attr_native_value = last.state
            self._channel_id = last.attributes.get("channel_id")
        self._store = Store(self.hass, 1, f"tvchannel.{self._entry.entry_id}.channel_ids")
        self._id_names = await self._store.async_load() or {}
        self._task = self.hass.async_create_background_task(
            self._run(), name=f"tvchannel_logcat_{self._entry.entry_id}",
        )
        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_poll_channel_id, timedelta(seconds=CHANNEL_ID_POLL_S))
        )

    async def _async_read_channel_id(self) -> str | None:
        """`settings get system recent_play_channel_id` through the androidtv integration's own
        connection. None if unreadable; the setting missing altogether ("null") switches polling
        off for this TV, whose firmware simply does not keep it."""
        try:
            await self.hass.services.async_call(
                "androidtv", "adb_command", {"entity_id": self._media_player, "command": ADB_COMMAND}, blocking=True,
            )
        except Exception:  # noqa: BLE001 - TV off / adb busy: just try again next time
            return None
        state = self.hass.states.get(self._media_player)
        response = ((state.attributes.get("adb_response") if state else None) or "").strip()
        if response.isdigit():
            self._id_supported = True
            return response
        if response == "null":
            self._id_supported = False
        return None

    async def _async_poll_channel_id(self, _now=None) -> None:
        if self._id_supported is False or not self._attr_available:
            return
        channel_id = await self._async_read_channel_id()
        if channel_id is None or channel_id == self._channel_id:
            return
        self._channel_id = channel_id
        name = self._id_names.get(channel_id)
        if name and name != self._attr_native_value:
            _LOGGER.debug("tvchannel: channel id %s -> %s (from poll)", channel_id, name)
            self._attr_native_value = name
        self.async_write_ha_state()

    async def _async_learn_id_for(self, name: str) -> None:
        """Right after the log names a channel, read the id it now has and remember the pair."""
        if self._id_supported is False:
            return
        channel_id = await self._async_read_channel_id()
        if channel_id is None or self._attr_native_value != name:
            return
        self._channel_id = channel_id
        if self._id_names.get(channel_id) != name:
            self._id_names[channel_id] = name
            await self._store.async_save(self._id_names)
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _run(self) -> None:
        # Imported here, not at module level — this is the one entity in the integration that
        # actually needs adb_shell directly (MODE_SETTINGS_KEY goes through the androidtv
        # integration's own existing connection instead), so the rest of the integration doesn't
        # fail to load if this optional dependency is ever slow to install on first setup.
        from adb_shell.adb_device_async import AdbDeviceTcpAsync
        from adb_shell.exceptions import AdbCommandFailureException, TcpTimeoutException

        adbkey_path = self._adbkey_path or self.hass.config.path(ANDROIDTV_ADBKEY_DIR, ANDROIDTV_ADBKEY_FILENAME)
        try:
            rsa_keys = [await self.hass.async_add_executor_job(_load_or_generate_key, adbkey_path)]
        except Exception:  # noqa: BLE001 - a broken/unwritable key path is fatal for this entity, but not for HA
            _LOGGER.exception("tvchannel: could not load or generate an adb key at %s", adbkey_path)
            self._attr_available = False
            self.async_write_ha_state()
            return

        buffer = ""
        while True:
            device = AdbDeviceTcpAsync(self._host, self._port, default_transport_timeout_s=9.0)
            connected = False
            started = asyncio.get_running_loop().time()
            try:
                # rsa_keys reuses the SAME key Home Assistant's own androidtv integration already
                # generated and got authorized on the TV by default (see const.py) — so on a TV
                # that's already paired with that integration, this needs no new on-screen tap at
                # all. Confirmed live: without a matching authorized key, the TV's standard "Allow
                # USB debugging from this computer?" system dialog appears and blocks the connect
                # until answered — exactly the one-time step the existing androidtv integration's
                # own setup already went through for this same key.
                await device.connect(rsa_keys=rsa_keys, auth_timeout_s=10.0)
                _LOGGER.debug("tvchannel: connected to %s:%s for logcat streaming", self._host, self._port)
                # Available as soon as the TV answers, not only once a matching line arrives: after
                # a long quiet spell (no channel change for _READ_TIMEOUT_S) the stream is simply
                # reopened, and by then the last channel line has long rotated out of the TV's log
                # buffer — waiting for one left the entity unavailable until the next channel
                # change. No change was logged meanwhile, so the last known channel still holds.
                connected = True
                buffer = ""
                if not self._attr_available:
                    self._attr_available = True
                    self.async_write_ha_state()
                    # Back from a restart or standby: ask which channel is on right away.
                    self.hass.async_create_task(self._async_poll_channel_id())
                # transport_timeout_s must be set here too, not just read_timeout_s — it's the
                # actual per-read socket timeout (read_timeout_s is only an overall retry budget
                # on top of it). Left unset, it falls back to the connection's own
                # default_transport_timeout_s (9s, sized for one-shot commands), so any normal
                # quiet period on the channel — the TV only logs a burst right after a channel
                # change, then nothing until the next one — was wrongly treated as a dead
                # connection and reconnected every ~9s, flipping the entity unavailable almost
                # immediately after every channel change instead of holding the value.
                async for chunk in device.streaming_shell(
                    LOGCAT_COMMAND, transport_timeout_s=_READ_TIMEOUT_S, read_timeout_s=_READ_TIMEOUT_S,
                ):
                    self._attr_available = True
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        match = _PROGRAM_NAME_RE.search(line)
                        if match:
                            # Whichever alternative in PROGRAM_NAME_PATTERN matched (one per known
                            # firmware's line shape — see const.py) is the only group with content.
                            name = next(g for g in match.groups() if g is not None).strip()
                            if name and name != self._attr_native_value:
                                self._attr_native_value = name
                                self.async_write_ha_state()
                            if name:
                                # The setting updates a moment after the tune is logged.
                                async_call_later(
                                    self.hass, 2,
                                    lambda _t, n=name: self.hass.async_create_task(self._async_learn_id_for(n)),
                                )
                        # Independent of the channel-name match above — a line can only ever match
                        # one of the two patterns, so this never interferes with it either way.
                        if self._program_sensor is not None:
                            program_match = _CURRENT_PROGRAM_RE.search(line)
                            if program_match:
                                self._program_sensor.update_from_match(program_match)
            except asyncio.CancelledError:
                raise
            except (AdbCommandFailureException, TcpTimeoutException, OSError, EOFError) as err:
                _LOGGER.debug("tvchannel: logcat stream to %s:%s dropped (%s) — reconnecting in %ss",
                               self._host, self._port, err, RECONNECT_DELAY_S)
            except Exception:  # noqa: BLE001 - any other failure still just means "reconnect", never crash the task
                _LOGGER.exception("tvchannel: unexpected error streaming logcat from %s:%s", self._host, self._port)
            finally:
                with contextlib.suppress(Exception):
                    await device.close()
            # A stream that connected and then ended (quiet-period timeout, TV dropping an idle
            # socket) is just reopened, keeping the last values; only a TV that can't be reached
            # at all marks the entities unavailable.
            if not connected:
                if self._attr_available:
                    self._attr_available = False
                    self.async_write_ha_state()
                if self._program_sensor is not None:
                    self._program_sensor.set_available(False)
            # Reopen a long-lived stream right away; back off on anything that failed or ended fast.
            long_lived = connected and asyncio.get_running_loop().time() - started >= 60
            await asyncio.sleep(1 if long_lived else RECONNECT_DELAY_S)


class TvChannelProgramSensor(SensorEntity):
    """MODE_LOGCAT_STREAM, TV1-style firmware only — the actual EPG entry (title, start/end,
    description, genre, rating) for whatever's airing right now on the tuned channel, not just the
    channel name. Fed by TvChannelLogcatSensor's own read loop via update_from_match(); on the
    other firmware, EpgManager simply never appears in the log and this entity just stays
    unavailable, harmlessly. A separate entity from the channel-name one on purpose, so this one's
    parsing can never affect that one.
    """

    _attr_icon = "mdi:television-guide"
    _attr_has_entity_name = True
    _attr_translation_key = "current_program"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self._attr_unique_id = f"{entry.entry_id}_current_program"
        self._attr_device_info = _device_info(entry, get_config(entry, CONF_MEDIA_PLAYER))
        self._attr_available = False
        self._attr_native_value: str | None = None
        self._attr_extra_state_attributes: dict[str, str] = {}

    def set_available(self, available: bool) -> None:
        if self._attr_available != available:
            self._attr_available = available
            if self.hass is not None:
                self.async_write_ha_state()

    def update_from_match(self, match: re.Match) -> None:
        title = match.group("title").strip()
        if not title or title == self._attr_native_value:
            return
        self._attr_native_value = title
        self._attr_available = True
        self._attr_extra_state_attributes = {
            "start_time": dt_util.utc_from_timestamp(int(match.group("start")) / 1000).isoformat(),
            "end_time": dt_util.utc_from_timestamp(int(match.group("end")) / 1000).isoformat(),
            "description": match.group("description").strip(),
            "genre": match.group("genre").strip(),
            "rating": match.group("rating"),
            "channel_id": match.group("channel_id"),
        }
        if self.hass is not None:
            self.async_write_ha_state()
