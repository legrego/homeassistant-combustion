"""Constants for the Combustion integration."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "combustion"
NAME = "Combustion MeatNet"
MANUFACTURER = "Combustion, Inc."

# A single config entry covers every Combustion device in range, so it has a fixed unique ID.
MEATNET_UNIQUE_ID = "combustion_meatnet"

CONF_ENABLE_PREDICTIONS = "enable_predictions"

# Seconds without any advertisement, direct or relayed, before a device is unavailable. Devices
# advertise several times a second, but Home Assistant forwards each address only from the one
# scanner that owns it and drops copies from every other scanner. A weak or busy owner can pass
# along an advertisement only every 30 to 120 seconds, so the library's 15 second default made
# probes flap between available and unavailable.
STALE_TIMEOUT = 120
