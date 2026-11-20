"""Constants for the Combustion integration."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "combustion"
NAME = "Combustion MeatNet"
MANUFACTURER = "Combustion, Inc."

# A single config entry covers every Combustion device in range, so it has a fixed unique ID.
MEATNET_UNIQUE_ID = "combustion_meatnet"
