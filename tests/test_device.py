"""Devices as entities see them, read from the library's Bluetooth devices."""

from itertools import product

import pytest
from combustion_ble import (
    CookingPrediction,
    FoodSafe,
    FoodSafeMode,
    FoodSafeState,
    MeatNet,
    PredictionMode,
    PredictionState,
    PredictionType,
    Serving,
    SimplifiedProduct,
)

from custom_components.combustion.device import (
    FOOD_SAFETY_STATUSES,
    PREDICTION_STATUSES,
    GaugeDevice,
    ProbeDevice,
    ProbeFoodSafety,
    ProbePrediction,
)
from tests.utils.bt_utils import (
    GAUGE_ADDRESS,
    PROBE_ADDRESS,
    PROBE_SERIAL,
    gauge_payload,
    probe_payload,
)


def make_probe(**payload) -> ProbeDevice:
    """Return a probe heard over Bluetooth."""
    meatnet = MeatNet()
    meatnet.handle_advertisement(PROBE_ADDRESS, probe_payload(**payload), -61)
    (heard,) = meatnet.probes.values()
    probe = ProbeDevice(heard.serial_number)
    probe.async_set_bluetooth(heard)
    return probe


@pytest.mark.parametrize(
    ("serial_number", "identifier", "printed"),
    [(PROBE_SERIAL, "10001ccc", "10001CCC"), (0x1CCC, "1ccc", "00001CCC")],
)
def test_probe_identity(serial_number: int, identifier: str, printed: str) -> None:
    """Identifiers drop leading zeros, unlike serial numbers printed on probes."""
    probe = make_probe(serial_number=serial_number)
    assert (probe.identifier, probe.serial_number) == (identifier, printed)
    assert probe.model == "Predictive Thermometer"


def test_probe_readings() -> None:
    """Virtual sensors read the thermistors the probe chose, as the library does."""
    for core, surface, ambient in product(range(1, 7), range(4, 8), range(5, 9)):
        probe = make_probe(core=core, surface=surface, ambient=ambient)
        readings = probe.readings
        assert (
            readings.core_thermistor,
            readings.surface_thermistor,
            readings.ambient_thermistor,
        ) == (core, surface, ambient)
        assert (readings.core, readings.surface, readings.ambient) == (
            probe.bluetooth.core_temperature,
            probe.bluetooth.surface_temperature,
            probe.bluetooth.ambient_temperature,
        )
    assert [readings.thermistor(n) for n in range(1, 9)] == pytest.approx(
        [20.0, 21.1, 22.2, 23.3, 24.4, 25.5, 26.6, 27.7]
    )
    assert probe.instant_read is None
    assert probe.rssi == -61


def test_probe_without_readings() -> None:
    """A probe only heard in instant read mode has no temperatures yet."""
    probe = make_probe(instant_read=55.0, low_battery=True)
    assert probe.instant_read == pytest.approx(55, abs=1)
    readings = probe.readings
    assert readings.core is readings.thermistor(8) is None
    assert probe.low_battery


def test_gauge() -> None:
    """A gauge has one temperature."""
    meatnet = MeatNet()
    meatnet.handle_advertisement(GAUGE_ADDRESS, gauge_payload(low_battery=True), -70)
    gauge = GaugeDevice(meatnet.gauges["GG00000001"])
    assert gauge.identifier == gauge.serial_number == "GG00000001"
    assert gauge.model == "Giant Grill Gauge"
    assert gauge.temperature == pytest.approx(150)
    assert gauge.low_battery
    assert gauge.rssi == -70
    assert gauge.available


def test_listeners() -> None:
    """Listeners follow the device until they are removed."""
    meatnet = MeatNet()
    meatnet.handle_advertisement(PROBE_ADDRESS, probe_payload())
    probe = ProbeDevice(PROBE_SERIAL)
    probe.async_set_bluetooth(meatnet.probes[PROBE_SERIAL])
    calls: list[float | None] = []
    remove = probe.async_add_listener(lambda: calls.append(probe.readings.core))
    meatnet.handle_advertisement(PROBE_ADDRESS, probe_payload(temperatures=[60.0] * 8))
    remove()
    meatnet.handle_advertisement(PROBE_ADDRESS, probe_payload(temperatures=[70.0] * 8))
    assert calls == pytest.approx([60])


@pytest.mark.parametrize("state", PredictionState)
@pytest.mark.parametrize("mode", PredictionMode)
def test_prediction(mode: PredictionMode, state: PredictionState) -> None:
    """Predictions without a supported target are idle, and unfamiliar states are unknown."""
    probe = make_probe()
    assert probe.prediction is None
    probe.bluetooth.prediction = CookingPrediction(
        state=state,
        mode=mode,
        type=PredictionType.REMOVAL,
        set_point_temperature=65.0,
        estimated_core_temperature=40.0,
        seconds_remaining=120,
        percent_through_cook=50,
    )
    if mode in (PredictionMode.TIME_TO_REMOVAL, PredictionMode.REMOVAL_AND_RESTING):
        known = {
            PredictionState.PROBE_NOT_INSERTED: "probe_not_inserted",
            PredictionState.PROBE_INSERTED: "probe_inserted",
            PredictionState.COOKING: "warming",
            PredictionState.PREDICTING: "predicting",
            PredictionState.REMOVAL_PREDICTION_DONE: "ready",
        }
        assert probe.prediction == ProbePrediction(known.get(state), 65.0, 120)
    else:
        assert probe.prediction == ProbePrediction("idle", None, 120)
    assert probe.prediction.status in (None, *PREDICTION_STATUSES)


def food_safe(**changes) -> FoodSafe:
    """Build SafeCook data for an Integrated program that is part of the way to safe."""
    return FoodSafe(
        **{
            "state": FoodSafeState.NOT_SAFE,
            "log_reduction": 2.3,
            "seconds_above_threshold": 300,
            "mode": FoodSafeMode.INTEGRATED,
            "product": 2,
            "serving": Serving.SERVED_IMMEDIATELY,
            "threshold_temperature": 54.4,
            "z_value": 7.0,
            "reference_temperature": 70.0,
            "d_value": 0.2,
            "target_log_reduction": 6.5,
            **changes,
        }
    )


SIMPLIFIED = {
    "mode": FoodSafeMode.SIMPLIFIED,
    "product": SimplifiedProduct.ANY_POULTRY,
    "log_reduction": 0.0,
    "z_value": 0.0,
    "reference_temperature": 0.0,
    "d_value": 0.0,
    "target_log_reduction": 0.0,
}


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        (None, None),
        (food_safe(), ProbeFoodSafety("not_safe", 54.4, 300, 2.3, 6.5)),
        (
            food_safe(state=FoodSafeState.SAFE),
            ProbeFoodSafety("safe", 54.4, 300, 2.3, 6.5),
        ),
        (
            food_safe(state=FoodSafeState.SAFETY_IMPOSSIBLE, **SIMPLIFIED),
            ProbeFoodSafety("safety_impossible", 54.4, 300),
        ),
        # The program leaves the threshold to the firmware.
        (
            food_safe(threshold_temperature=0.0, **SIMPLIFIED),
            ProbeFoodSafety("not_safe", None, 300),
        ),
        # Unfamiliar states and modes stay plain integers.
        (food_safe(state=5), ProbeFoodSafety(None, 54.4, 300, 2.3, 6.5)),
        (food_safe(mode=5), ProbeFoodSafety("not_safe", 54.4, 300)),
        # Off is an all-zero program, whatever its progress.
        (
            food_safe(
                threshold_temperature=0.0,
                **(SIMPLIFIED | {"product": SimplifiedProduct.DEFAULT}),
            ),
            ProbeFoodSafety("off"),
        ),
    ],
)
def test_food_safety(
    reported: FoodSafe | None, expected: ProbeFoodSafety | None
) -> None:
    """SafeCook progress is reported only as far as the program defines it."""
    probe = make_probe()
    probe.bluetooth.food_safe = reported
    assert probe.food_safety == expected
    if expected is not None:
        assert expected.status in (None, *FOOD_SAFETY_STATUSES)
