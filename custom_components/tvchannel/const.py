"""Constants for the TV Channel Watcher integration."""

DOMAIN = "tvchannel"

CONF_MEDIA_PLAYER = "media_player_entity_id"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_MODE = "mode"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_ADBKEY_PATH = "adbkey_path"

DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 600
DEFAULT_PORT = 5555

# Home Assistant's own androidtv integration generates (if missing) and reuses an ADB key pair at
# exactly this path — hass.config.path(ANDROIDTV_ADBKEY_DIR, ANDROIDTV_ADBKEY_FILENAME) — shared
# across every androidtv config entry, confirmed by reading that integration's own source. Since
# MODE_LOGCAT_STREAM opens its own, separate connection rather than going through the androidtv
# integration, it needs its own copy of a working key — defaulting to THIS exact path means it
# reuses the SAME already-authorized key by default, so a TV that's already paired with the
# existing androidtv integration needs no new "Allow USB debugging?" tap on screen at all.
ANDROIDTV_ADBKEY_DIR = ".storage"
ANDROIDTV_ADBKEY_FILENAME = "androidtv_adbkey"

MODE_SETTINGS_KEY = "settings_key"
MODE_LOGCAT_STREAM = "logcat_stream"
MODES = [MODE_SETTINGS_KEY, MODE_LOGCAT_STREAM]

# TCL's live-TV app (com.tcl.tv) tracks the currently-tuned channel's raw numeric row id from
# Android's TvContract channel database in this one system setting on SOME TCL firmware builds —
# cheap to read (a single `settings get`, a few bytes of adb traffic), unlike the channel database
# itself, which needs a signature-level EPG permission that isn't obtainable without root.
# Confirmed present on one TV, confirmed ABSENT entirely on another (different TCL firmware
# branch, different TvInputService implementation) — MODE_LOGCAT_STREAM exists for that case.
ADB_COMMAND = "settings get system recent_play_channel_id"

# On the firmware that has no recent_play_channel_id setting at all, the live-TV app instead logs
# this line (at debug level) every single time the channel actually changes — confirmed live, both
# via the remote and via an adb-simulated channel-up/down key press. There's no equivalent
# "ask what's showing right now" call anywhere in this firmware's own framework (traced through
# its actual compiled AIDL service — it's purely a push/callback design with no synchronous
# getter), so this is the only observed way to learn the channel on this specific firmware, and it
# only works as a continuously-tailed stream (see coordinatorless streaming sensor in sensor.py) —
# polling for it misses updates entirely once the device's own very high log volume rotates the
# line out of the buffer, which confirmed happens within well under 10 minutes.
#
# Two different TCL firmware branches confirmed live, each with its own tag and line shape — both
# are filtered for and matched at once so MODE_LOGCAT_STREAM works unmodified on either TV, not
# just the one it was originally built for:
#   - com.tcl.tv.TVActivity  → "...programName=MEGA HD, provideName=..."
#   - TvChannelManager       → "getCurrentProgramInfo name = MAK TV HD,key = 24,channelId = ..."
#
# EpgManager is a third, independent tag (confirmed live on the TV1-style firmware only — filtering
# for it on the other firmware simply never matches anything, harmlessly) that additionally logs
# the actual EPG entry for whatever's airing right now on the tuned channel — not just the channel
# name, but the programme's own title/description/start-end times. It feeds a second, separate
# entity (current-program sensor.py's TvChannelProgramSensor) purely additively; it does not
# replace or touch the channel-name matching above in any way.
LOGCAT_TAGS = ["com.tcl.tv.TVActivity", "TvChannelManager", "EpgManager"]
LOGCAT_COMMAND = "logcat -s " + " ".join(f"{tag}:D" for tag in LOGCAT_TAGS)
# Either line shape, group(1) or group(2) — whichever matched — is the channel name, trimmed.
PROGRAM_NAME_PATTERN = r"programName=([^,]*),|getCurrentProgramInfo name = ([^,]*),"

# Matches e.g.:
#   current EpgInfo: EpgInfo(..., cId=10019866, ..., title='[K8] Ρεπορτάζ Τώρα!', ...,
#   startTime=1790162100000, endTime=1790167500000 , description='', canonicalGenres='Καμία',
#   ..., rating=8, ...)
# startTime/endTime are epoch milliseconds. A blank title (isEmpty EPG slot) is filtered out by
# the sensor, not by this pattern, same as an empty channel name already is above.
CURRENT_PROGRAM_PATTERN = (
    r"current EpgInfo: EpgInfo\(.*?cId=(?P<channel_id>\d+).*?"
    r"title='(?P<title>[^']*)'.*?"
    r"startTime=(?P<start>\d+), endTime=(?P<end>\d+).*?"
    r"description='(?P<description>[^']*)'.*?"
    r"canonicalGenres='(?P<genre>[^']*)'.*?"
    r"rating=(?P<rating>\d+)"
)

# How long to wait before retrying the persistent connection after it drops (TV reboot, network
# blip, Wi-Fi roam, ...) — not instant, so a flaky link doesn't spin in a tight reconnect loop.
RECONNECT_DELAY_S = 15

# How often MODE_LOGCAT_STREAM also reads recent_play_channel_id (on TVs that keep it) to stay
# right when the log cannot help: after an HA restart or the TV waking from standby.
CHANNEL_ID_POLL_S = 60
