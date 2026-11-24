"""Probes reported by MeatNet Cloud as entities see them, without Home Assistant."""

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import PropertyMock, patch

import pytest
from combustion_ble import (
    CookingPrediction,
    MeatNet,
    PredictionMode,
    PredictionState,
    PredictionType,
)
from combustion_cloud import DeviceStatus

from custom_components.combustion import device
from custom_components.combustion.device import (
    FOOD_SAFETY_STATUSES,
    PREDICTION_STATUSES,
    ProbeDevice,
    ProbeFoodSafety,
    ProbePrediction,
    ProbeReadings,
)
from tests.test_device import food_safe
from tests.utils.bt_utils import PROBE_ADDRESS, PROBE_SERIAL, probe_payload
from tests.utils.cloud_utils import status_data

NOW = datetime(2026, 11, 23, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def now(freezer) -> None:
    """Stop the clock."""
    freezer.move_to(NOW)


def cloud_status(*, age: float = 0, **changes: Any) -> DeviceStatus:
    """Build a cloud sample from a payload with these changes."""
    return DeviceStatus.from_data(status_data(age=age) | changes)


def cloud_probe(**kwargs: Any) -> ProbeDevice:
    """Build a probe that only the cloud reports."""
    probe = ProbeDevice(PROBE_SERIAL)
    probe.async_set_cloud(cloud_status(**kwargs))
    return probe


def hear(probe: ProbeDevice, **payload: Any) -> MeatNet:
    """Let Bluetooth hear a probe."""
    # The clock that the probe's age is measured with, which is stopped here.
    meatnet = MeatNet(clock=device.monotonic)
    meatnet.handle_advertisement(PROBE_ADDRESS, probe_payload(**payload), -61)
    probe.async_set_bluetooth(meatnet.probes[PROBE_SERIAL])
    return meatnet


@pytest.mark.parametrize(
    ("readings_age", "cloud_age", "source"),
    [
        (None, None, None),
        (0, None, "bluetooth"),
        # Bluetooth readings are only dated while the probe is available.
        (119, None, "bluetooth"),
        (None, 0, "cloud"),
        (None, 120, "cloud"),
        (None, 120.1, None),
        # Bluetooth is preferred for a while, even over newer cloud samples.
        (0, 0, "bluetooth"),
        (30, 0, "bluetooth"),
        (30.1, 0, "cloud"),
        # After that, the newest wins.
        (60, 59, "cloud"),
        (60, 60, "bluetooth"),
        (60, 61, "bluetooth"),
        (119, 120.1, "bluetooth"),
        (20, 60, "bluetooth"),
    ],
)
def test_source(
    readings_age: float | None, cloud_age: float | None, source: str | None
) -> None:
    """Readings come from Bluetooth if it is recent, and otherwise from the newest source."""
    probe = ProbeDevice(PROBE_SERIAL)
    with (
        # Every advertisement carried readings.
        patch.object(
            ProbeDevice, "bluetooth_age", PropertyMock(return_value=readings_age)
        ),
        patch.object(
            ProbeDevice,
            "bluetooth_readings_age",
            PropertyMock(return_value=readings_age),
        ),
        patch.object(ProbeDevice, "cloud_age", PropertyMock(return_value=cloud_age)),
    ):
        assert probe.source == source
        assert probe.available == (source is not None)


@pytest.mark.parametrize(
    ("readings_age", "cloud_age", "source"),
    [
        # Nothing but instant read advertisements.
        (None, None, "bluetooth"),
        (None, 0, "cloud"),
        (None, 120.1, "bluetooth"),
        # Instant read advertisements since the last one with temperatures.
        (30, 0, "bluetooth"),
        (31, 0, "cloud"),
        (60, 59, "cloud"),
        (60, 61, "bluetooth"),
        (500, 100, "cloud"),
        (500, 120.1, "bluetooth"),
    ],
)
def test_source_in_instant_read(
    readings_age: float | None, cloud_age: float | None, source: str
) -> None:
    """An advertisement without temperatures doesn't make Bluetooth's any newer."""
    probe = ProbeDevice(PROBE_SERIAL)
    with (
        patch.object(ProbeDevice, "bluetooth_age", PropertyMock(return_value=0)),
        patch.object(
            ProbeDevice,
            "bluetooth_readings_age",
            PropertyMock(return_value=readings_age),
        ),
        patch.object(ProbeDevice, "cloud_age", PropertyMock(return_value=cloud_age)),
    ):
        assert probe.source == source
        assert probe.available


def test_unreported_probe() -> None:
    """A probe that nothing has reported has no values."""
    probe = ProbeDevice(PROBE_SERIAL)
    assert not probe.available
    assert probe.bluetooth is probe.cloud is None
    assert probe.bluetooth_age is probe.bluetooth_readings_age is None
    assert probe.cloud_age is None
    assert probe.low_battery is probe.rssi is probe.instant_read is None
    assert probe.prediction is probe.food_safety is None
    assert probe.readings == ProbeReadings()
    assert probe.readings.core is probe.readings.thermistor(8) is None
    assert probe.revisions == {}


def test_cloud_age(freezer) -> None:
    """Samples are dated by when they were taken, as far as that can be believed."""
    assert cloud_probe().cloud_age == 0
    assert cloud_probe(age=40).cloud_age == 40
    assert cloud_probe(age=28 * 86400).cloud_age == 28 * 86400
    assert cloud_probe(collected_timestamp=None).cloud_age is None
    assert cloud_probe(collected_timestamp="unreadable").cloud_age is None
    assert cloud_probe(timestamp=None).cloud_age == 0

    # An uploader whose clock is ahead can't make a sample newer than it can be.
    ahead = cloud_status(age=-50)
    probe = ProbeDevice(PROBE_SERIAL)
    probe.async_set_cloud(ahead)
    assert probe.cloud_age == 0
    probe.async_set_cloud(replace(ahead, published_at=NOW - timedelta(seconds=20)))
    assert probe.cloud_age == 20
    freezer.tick(10)
    probe.async_set_cloud(ahead)
    assert probe.cloud_age == 10
    probe.async_set_cloud(replace(ahead, received_at=NOW - timedelta(seconds=70)))
    assert probe.cloud_age is None  # It was from too far in the future when it arrived.
    assert not probe.available

    # This computer's clock being set back doesn't make ages negative.
    freezer.move_to(NOW - timedelta(minutes=5))
    probe.async_set_cloud(cloud_status(age=-30))
    freezer.move_to(NOW - timedelta(minutes=10))
    assert probe.cloud_age == 0


def test_bluetooth_age() -> None:
    """Bluetooth is dated by the last advertisement, until the probe is stale."""
    now = 100.0
    meatnet = MeatNet(stale_timeout=120, clock=lambda: now)
    meatnet.handle_advertisement(PROBE_ADDRESS, probe_payload(), -61)
    probe = ProbeDevice(PROBE_SERIAL)
    probe.async_set_bluetooth(meatnet.probes[PROBE_SERIAL])
    with patch("custom_components.combustion.device.monotonic", lambda: now):
        now += 50
        assert probe.bluetooth_age == 50
        assert probe.rssi == -61
        now += 71
        meatnet.expire()
        assert probe.bluetooth_age is None
        assert probe.rssi is None


def test_bluetooth_readings_age() -> None:
    """Bluetooth readings are dated by the last advertisement that carried any."""
    now = 100.0
    meatnet = MeatNet(stale_timeout=120, clock=lambda: now)
    probe = ProbeDevice(PROBE_SERIAL)
    with patch("custom_components.combustion.device.monotonic", lambda: now):
        meatnet.handle_advertisement(
            PROBE_ADDRESS, probe_payload(instant_read=60.0), -61
        )
        probe.async_set_bluetooth(meatnet.probes[PROBE_SERIAL])
        assert probe.bluetooth_age == 0
        assert probe.bluetooth_readings_age is None
        assert probe.readings.temperatures is None

        now += 5
        meatnet.handle_advertisement(PROBE_ADDRESS, probe_payload(), -61)
        probe.async_readings_heard()
        assert probe.bluetooth_readings_age == 0
        assert probe.readings.core == 20

        # Temperatures are kept through instant read, but not for longer than without it.
        for _ in range(2):
            now += 60
            meatnet.handle_advertisement(
                PROBE_ADDRESS, probe_payload(instant_read=60.0), -61
            )
            assert probe.bluetooth_age == 0
            assert probe.readings.core == 20
        assert probe.bluetooth_readings_age == 120
        now += 1
        assert probe.available
        assert probe.readings.temperatures is probe.readings.core is None
        assert probe.readings.core_thermistor == 1

        now += 120
        meatnet.expire()
        assert probe.bluetooth_readings_age is None


def test_bluetooth_readings_heard_first() -> None:
    """A probe first heard with readings has their age from the start."""
    probe = ProbeDevice(PROBE_SERIAL)
    hear(probe)
    assert probe.bluetooth_readings_age == pytest.approx(0, abs=5)


def test_cloud_readings() -> None:
    """Cloud readings mean the same as Bluetooth ones."""
    probe = cloud_probe(
        temperatures=[20.0, 21.1, 22.2, 23.3, 24.4, 25.5, 26.6, 27.7],
        virtual_sensors={
            "virtual_core": "T3",
            "virtual_surface": "T6",
            "virtual_ambient": "T8",
        },
    )
    cloud = probe.readings
    assert (cloud.core, cloud.surface, cloud.ambient) == (22.2, 25.5, 27.7)
    hear(probe, core=3, surface=6, ambient=8)
    bluetooth = probe.readings
    assert probe.source == "bluetooth"
    assert bluetooth is not cloud
    assert cloud.temperatures == pytest.approx(bluetooth.temperatures)
    assert replace(cloud, temperatures=None) == replace(bluetooth, temperatures=None)


@pytest.mark.parametrize("selectors", [None, {}, {"virtual_core": "T9"}])
def test_cloud_readings_without_selectors(selectors: dict | None) -> None:
    """A virtual sensor is unknown without a usable thermistor."""
    readings = cloud_probe(virtual_sensors=selectors).readings
    assert readings.core is readings.surface is readings.ambient is None
    assert readings.core_thermistor is None
    assert readings.thermistor(1) == 20


def test_cloud_readings_missing() -> None:
    """Missing readings are unknown."""
    readings = cloud_probe(temperatures=[20.0, None]).readings
    assert readings.thermistor(1) == readings.core == 20
    assert readings.thermistor(2) is readings.thermistor(3) is None
    assert readings.ambient is None
    for temperatures in (None, []):
        readings = cloud_probe(temperatures=temperatures).readings
        assert readings.temperatures is readings.thermistor(1) is None


@pytest.mark.parametrize(
    ("reported", "low"),
    [("OK", False), ("LOW_BATTERY", True), ("FUTURE", None), (None, None)],
)
def test_cloud_battery(reported: str | None, low: bool | None) -> None:
    """An unfamiliar battery status is unknown."""
    assert cloud_probe(battery_status=reported).low_battery is low


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, ProbePrediction("predicting", 60, 600)),
        ({"age": 12.7}, ProbePrediction("predicting", 60, 588)),
        (
            {"age": 12, "raw_prediction_seconds": 5},
            ProbePrediction("predicting", 60, 0),
        ),
        (
            {"prediction_mode": "REMOVAL_AND_RESTING"},
            ProbePrediction("predicting", 60, 600),
        ),
        (
            {"prediction_state": "PROBE_NOT_INSERTED"},
            ProbePrediction("probe_not_inserted", 60, None),
        ),
        (
            {"prediction_state": "PROBE_INSERTED"},
            ProbePrediction("probe_inserted", 60, None),
        ),
        ({"prediction_state": "COOKING"}, ProbePrediction("warming", 60, None)),
        (
            {"prediction_state": "REMOVAL_PREDICTION_DONE", "age": 10},
            ProbePrediction("ready", 60, 0),
        ),
        ({"prediction_state": "FUTURE"}, ProbePrediction(None, 60, None)),
        ({"prediction_state": None}, ProbePrediction(None, 60, None)),
        ({"raw_prediction_seconds": 131071}, ProbePrediction("predicting", 60, None)),
        ({"prediction_mode": "NONE"}, ProbePrediction("idle", None, None)),
        ({"prediction_mode": "FUTURE"}, ProbePrediction("idle", None, None)),
        ({"prediction_mode": None}, None),
        # Outdated well before temperatures are.
        ({"age": 29}, ProbePrediction("predicting", 60, 571)),
        ({"age": 30}, None),
        ({"collected_timestamp": None}, None),
    ],
)
def test_cloud_prediction(
    changes: dict[str, Any], expected: ProbePrediction | None
) -> None:
    """Recent cloud predictions mean the same as Bluetooth ones, less the sample's age."""
    prediction = cloud_probe(**changes).prediction
    assert prediction == expected
    if prediction is not None:
        assert prediction.status in (None, *PREDICTION_STATUSES)


PROGRAM = {
    "mode": "INTEGRATED",
    "product_type": "POULTRY",
    "serving": "IMMEDIATELY",
    "completion_criteria": {
        "select_thresh_ref_temp": 54.4,
        "z_value": 7.0,
        "ref_temp": 70.0,
        "dvalue_at_rt": 0.2,
        "target_log_reduction": 6.5,
    },
}
SIMPLIFIED = PROGRAM | {
    "mode": "SIMPLIFIED",
    "product_type": "ANY_POULTRY",
    "completion_criteria": None,
}
PROGRESS = {
    "state": "NOT_SAFE",
    "log_reduction": 2.3,
    "seconds_above_threshold": 300,
    "sequence_number": 7,
}


@pytest.mark.parametrize(
    ("program", "progress", "expected"),
    [
        (PROGRAM, PROGRESS, ProbeFoodSafety("not_safe", 54.4, 300, 2.3, 6.5)),
        (
            PROGRAM,
            PROGRESS | {"state": "SAFE"},
            ProbeFoodSafety("safe", 54.4, 300, 2.3, 6.5),
        ),
        (
            SIMPLIFIED,
            PROGRESS | {"state": "SAFETY_IMPOSSIBLE"},
            ProbeFoodSafety("safety_impossible", None, 300),
        ),
        # Simplified mode has no log reductions, even with criteria.
        (
            PROGRAM | {"mode": "SIMPLIFIED"},
            PROGRESS,
            ProbeFoodSafety("not_safe", 54.4, 300),
        ),
        (
            PROGRAM | {"completion_criteria": None},
            PROGRESS,
            ProbeFoodSafety("not_safe", None, 300, 2.3, None),
        ),
        (
            PROGRAM
            | {
                "completion_criteria": PROGRAM["completion_criteria"]
                | {"select_thresh_ref_temp": 0}
            },
            PROGRESS,
            ProbeFoodSafety("not_safe", None, 300, 2.3, 6.5),
        ),
        (
            PROGRAM,
            PROGRESS | {"state": "FUTURE"},
            ProbeFoodSafety(None, 54.4, 300, 2.3, 6.5),
        ),
        # Off, whatever its progress.
        (SIMPLIFIED | {"product_type": "DEFAULT"}, PROGRESS, ProbeFoodSafety("off")),
        (SIMPLIFIED | {"product_type": "DEFAULT"}, None, ProbeFoodSafety("off")),
        (PROGRAM, None, None),
        (None, PROGRESS, None),
        (None, None, None),
    ],
)
def test_cloud_food_safety(
    program: dict | None, progress: dict | None, expected: ProbeFoodSafety | None
) -> None:
    """Cloud SafeCook progress means the same as Bluetooth's, while it is recent."""
    changes = {"food_safe_data": program, "food_safe_status": progress}
    assert cloud_probe(**changes).food_safety == expected
    if expected is not None:
        assert expected.status in (None, *FOOD_SAFETY_STATUSES)
    assert cloud_probe(**changes, age=29).food_safety == expected
    assert cloud_probe(**changes, age=30).food_safety is None


def test_bluetooth_predictions_come_first() -> None:
    """A live connection supplies predictions and SafeCook, and the cloud fills in without one."""
    probe = cloud_probe()
    hear(probe)
    assert probe.source == "bluetooth"
    assert probe.prediction == ProbePrediction("predicting", 60, 600)
    assert probe.food_safety == ProbeFoodSafety("not_safe", None, 0)

    heard = probe.bluetooth
    heard.prediction = CookingPrediction(
        state=PredictionState.COOKING,
        mode=PredictionMode.TIME_TO_REMOVAL,
        type=PredictionType.REMOVAL,
        set_point_temperature=65.0,
        estimated_core_temperature=40.0,
        seconds_remaining=None,
        percent_through_cook=50,
    )
    heard.food_safe = food_safe()
    assert probe.prediction == ProbePrediction("warming", 65, None)
    assert probe.food_safety == ProbeFoodSafety("not_safe", 54.4, 300, 2.3, 6.5)

    # Even while readings come from the cloud.
    with patch.object(
        ProbeDevice, "bluetooth_readings_age", PropertyMock(return_value=40)
    ):
        assert probe.source == "cloud"
        assert probe.prediction.target == 65
        assert probe.food_safety.threshold_temperature == 54.4

    heard.prediction = heard.food_safe = None
    assert probe.prediction.target == 60
    assert probe.food_safety.threshold_temperature is None


def test_cloud_revisions() -> None:
    """Revisions come from samples recent enough for readings."""
    expected = {"cloud": ("v2.4.0", "v1.1-A1")}
    assert cloud_probe(age=120).revisions == expected
    assert cloud_probe(age=121).revisions == {}
    assert cloud_probe(hardware_revision=None).revisions == {"cloud": ("v2.4.0", None)}
    assert cloud_probe(firmware_version="").revisions == {"cloud": (None, "v1.1-A1")}
    assert cloud_probe(hardware_revision=None, firmware_version="").revisions == {}


def test_listeners() -> None:
    """Listeners hear of changes from either source, and only of changes."""
    probe = ProbeDevice(PROBE_SERIAL)
    calls: list[str | None] = []
    remove = probe.async_add_listener(lambda: calls.append(probe.source))
    probe.async_refresh()
    assert calls == []
    probe.async_set_cloud(cloud_status())
    assert calls == ["cloud"]
    probe.async_set_cloud(cloud_status())
    probe.async_refresh()
    assert calls == ["cloud"]
    for changes in (
        {"temperatures": [50.0] * 8},
        {"battery_status": "LOW_BATTERY"},
        {"raw_prediction_seconds": 400},
        {"food_safe_status": PROGRESS},
    ):
        probe.async_set_cloud(cloud_status())
        calls.clear()
        probe.async_set_cloud(cloud_status(**changes))
        assert calls == ["cloud"]
    probe.async_set_cloud(cloud_status())
    calls.clear()
    hear(probe)
    assert calls == ["bluetooth"]
    probe.async_set_cloud(None)
    assert len(calls) == 2  # The cloud's prediction is gone.
    remove()
    probe.async_set_cloud(cloud_status())
    assert len(calls) == 2


def test_listeners_follow_source() -> None:
    """Listeners hear of a change of source, even if both report the same."""
    probe = cloud_probe(temperatures=[20.0] * 8)
    hear(probe, temperatures=[20.0] * 8, core=1, surface=4, ambient=8)
    calls: list[str | None] = []
    probe.async_add_listener(lambda: calls.append(probe.source))
    readings = probe.readings
    with patch.object(
        ProbeDevice, "bluetooth_readings_age", PropertyMock(return_value=40)
    ):
        probe.async_refresh()
        assert probe.readings == readings
    assert calls == ["cloud"]


def test_listener_errors(caplog: pytest.LogCaptureFixture) -> None:
    """One listener's error doesn't keep others from updating."""
    probe = ProbeDevice(PROBE_SERIAL)
    calls: list[None] = []

    def broken() -> None:
        raise ValueError("Bug")

    probe.async_add_listener(broken)
    probe.async_add_listener(lambda: calls.append(None))
    with caplog.at_level(logging.ERROR):
        probe.async_set_cloud(cloud_status())
    assert len(calls) == 1
    assert "Error updating Probe(10001CCC)" in caplog.text
