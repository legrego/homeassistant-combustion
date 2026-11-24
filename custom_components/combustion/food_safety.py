"""SafeCook programs shared by selects and actions.

SafeCook comes from the same connected status as predictions, so it shares their option.
"""

from __future__ import annotations

from collections.abc import Callable

from combustion_ble import (
    FoodSafe,
    FoodSafeMode,
    FoodSafeProgram,
    IntegratedProduct,
    Probe,
    Serving,
    SimplifiedProduct,
)


def _program_value[T](
    fn: Callable[[FoodSafe], T | None],
) -> Callable[[Probe | None], T | None]:
    """Read from the probe's SafeCook program.

    Unknown until a connection reports SafeCook data, and while SafeCook is off.
    """

    def value(probe: Probe | None) -> T | None:
        if probe is None:  # Not heard over Bluetooth.
            return None
        if (food_safe := probe.food_safe) is None or not food_safe.program_set:
            return None
        return fn(food_safe)

    return value


_PRODUCT_PREFIXES = {SimplifiedProduct: "simplified", IntegratedProduct: "integrated"}


def _product_option(product: SimplifiedProduct | IntegratedProduct) -> str:
    # Both modes number their products from 0, so options name the mode too.
    return f"{_PRODUCT_PREFIXES[type(product)]}_{product.name.lower()}"


PRODUCTS = [
    _product_option(product)
    for product_type in _PRODUCT_PREFIXES
    for product in product_type
]
"""Every product a probe can report, as options."""

SERVINGS = {
    "served_immediately": Serving.SERVED_IMMEDIATELY,
    "cooked_and_chilled": Serving.COOKED_AND_CHILLED,
}

SETTABLE_PRODUCTS = {
    _product_option(product): product
    for product in SimplifiedProduct
    # Simplified mode carries no parameters, so the Default product served immediately would
    # encode as all zeros, which the probe reads as SafeCook off.
    if product is not SimplifiedProduct.DEFAULT
}
"""Products whose programs can be written from Home Assistant, by option."""


type Program = FoodSafe | FoodSafeProgram
"""A program reported by a probe, or one written to it."""


def product_option(program: Program) -> str | None:
    """Return the program's product as an option, if this integration knows it."""
    product = program.product
    # Unknown products are kept as plain integers.
    if isinstance(product, SimplifiedProduct | IntegratedProduct):
        return _product_option(product)
    return None


def serving_option(program: Program) -> str | None:
    """Return the program's serving as an option, if this integration knows it."""
    if isinstance(program.serving, Serving):
        return program.serving.name.lower()
    return None


current_product = _program_value(product_option)
"""The probe's product option, unknown while SafeCook is off."""

current_serving = _program_value(serving_option)
"""The probe's serving option, unknown while SafeCook is off."""


def simplified_program(product: SimplifiedProduct, serving: Serving) -> FoodSafeProgram:
    """Build a Simplified program.

    The probe's firmware applies the USDA rules for the product, as Combustion's own apps rely on,
    so the program carries no temperatures or targets.
    """
    return FoodSafeProgram(
        mode=FoodSafeMode.SIMPLIFIED,
        product=product,
        serving=serving,
        threshold_temperature=0,
        z_value=0,
        reference_temperature=0,
        d_value=0,
        target_log_reduction=0,
    )
