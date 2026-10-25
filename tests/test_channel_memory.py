"""The channel sensor must keep (or recover) the channel when the TV's log can't tell it: across an
HA restart, and through `recent_play_channel_id` plus the id -> name map it learns from the log."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, mock_restore_cache

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from custom_components.tvchannel.const import DOMAIN

TV = "media_player.tcl_tv_adb"


class FakeTv:
    """Stands in for the androidtv integration: answers adb_command with the current setting."""

    def __init__(self, hass, channel_id="10019864"):
        self.hass, self.channel_id, self.calls = hass, channel_id, 0
        hass.states.async_set(TV, "on", {})

    def install(self):
        # after setup: the real androidtv integration (a dependency) registers its own service
        self.hass.services.async_register("androidtv", "adb_command", self._handle)

    async def _handle(self, call):
        self.calls += 1
        self.hass.states.async_set(TV, "on", {"adb_response": self.channel_id})


async def _setup(hass):
    """Sets the entry up with the logcat loop replaced by one that just marks the stream connected."""
    from custom_components.tvchannel.sensor import TvChannelLogcatSensor

    async def fake_run(self):
        self._attr_available = True
        self.async_write_ha_state()
        self.hass.async_create_task(self._async_poll_channel_id())
        hass.data.setdefault("tv_sensor", self)

    entry = MockConfigEntry(domain=DOMAIN, data={
        "media_player_entity_id": TV, "mode": "logcat_stream", "host": "1.2.3.4", "port": 5555, "scan_interval": 30,
    })
    entry.add_to_hass(hass)
    with patch.object(TvChannelLogcatSensor, "_run", fake_run):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return hass.data["tv_sensor"]


async def test_learns_id_then_recovers_name_by_polling(hass: HomeAssistant):
    tv = FakeTv(hass)
    sensor = await _setup(hass)
    tv.install()
    # the log names a channel -> the id it has is learned 2s later
    sensor._attr_native_value = "MEGA HD"
    sensor.async_write_ha_state()
    await sensor._async_learn_id_for("MEGA HD")
    assert sensor._id_names == {"10019864": "MEGA HD"}
    # user switches channel twice; the second change's log line is missed
    tv.channel_id = "10019870"
    sensor._attr_native_value = "STAR HD"
    await sensor._async_learn_id_for("STAR HD")
    tv.channel_id = "10019864"  # back on MEGA, nothing logged
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    st = hass.states.get(sensor.entity_id)
    assert st.state == "MEGA HD" and st.attributes["channel_id"] == "10019864"


async def test_restored_after_restart(hass: HomeAssistant):
    FakeTv(hass, channel_id="555")
    mock_restore_cache(hass, [State("sensor.tv_channel_watcher_media_player_tcl_tv_adb_channel_name", "ERT1 HD", {"channel_id": "555"})])
    sensor = await _setup(hass)
    st = hass.states.get(sensor.entity_id)
    assert st.state == "ERT1 HD"


async def test_tv_without_setting_stops_polling(hass: HomeAssistant):
    tv = FakeTv(hass, channel_id="null")
    sensor = await _setup(hass)
    tv.install()
    await sensor._async_poll_channel_id()
    assert sensor._id_supported is False
    n = tv.calls
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=200))
    await hass.async_block_till_done()
    assert tv.calls == n
