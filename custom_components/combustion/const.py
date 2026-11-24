"""Constants for the Combustion integration."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "combustion"
NAME = "Combustion MeatNet"
MANUFACTURER = "Combustion, Inc."
PROBE_MODEL = "Predictive Thermometer"
GAUGE_MODEL = "Giant Grill Gauge"

# A single config entry covers every Combustion device in range, so it has a fixed unique ID.
MEATNET_UNIQUE_ID = "combustion_meatnet"

CONF_ENABLE_PREDICTIONS = "enable_predictions"

# The linked MeatNet Cloud account, in the entry's data. Its email is stored alongside these.
CONF_CLOUD = "cloud"
CONF_ACCOUNT_ID = "account_id"
CONF_REFRESH_TOKEN = "refresh_token"

# Seconds without any advertisement, direct or relayed, before a device is unavailable. Devices
# advertise several times a second, but Home Assistant forwards each address only from the one
# scanner that owns it and drops copies from every other scanner. A weak or busy owner can pass
# along an advertisement only every 30 to 120 seconds, so the library's 15 second default made
# probes flap between available and unavailable.
STALE_TIMEOUT = 120

# Seconds since a probe was last heard over Bluetooth that its Bluetooth readings are kept over
# newer ones from MeatNet Cloud. Switching sooner would follow a weak Bluetooth signal back and
# forth.
BLUETOOTH_PREFERENCE = 30

# Seconds that cloud predictions and SafeCook progress are shown for. They change with every sample,
# which arrive about every 5 seconds, so they are outdated well before temperatures are.
CLOUD_PREDICTION_TIMEOUT = 30

# Seconds that a cloud sample's time may be ahead of this computer's clock and still be believed.
# Sample times come from whichever phone or WiFi accessory uploaded the reading.
CLOUD_CLOCK_SKEW = 60

SOURCE_BLUETOOTH = "bluetooth"
SOURCE_CLOUD = "cloud"
