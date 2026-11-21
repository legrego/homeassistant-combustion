"""Global fixtures for the Combustion integration."""

from collections.abc import Generator
from functools import partial
from unittest.mock import AsyncMock, patch

import pytest
from bleak.exc import BleakError
from combustion_ble import MeatNet

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def mock_bluetooth(enable_bluetooth):
    """Auto mock bluetooth."""


@pytest.fixture(autouse=True)
def no_real_connections():
    """Fail Bluetooth connections that a test hasn't faked, such as brief revision reads."""
    with patch(
        "custom_components.combustion.prediction.establish_connection",
        AsyncMock(side_effect=BleakError("Tests make no real connections")),
    ):
        yield


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Auto enable custom integrations."""
    yield


class FakeClock:
    """Monotonic clock for MeatNet that tests advance by hand."""

    def __init__(self) -> None:
        """Initialize."""
        self.now = 1000.0

    def __call__(self) -> float:
        """Return the current time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.now += seconds


@pytest.fixture
def clock() -> Generator[FakeClock]:
    """Give the integration's MeatNet a clock that tests control."""
    fake = FakeClock()
    with patch(
        "custom_components.combustion.coordinator.MeatNet", partial(MeatNet, clock=fake)
    ):
        yield fake
