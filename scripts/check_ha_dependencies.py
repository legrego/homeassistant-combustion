"""Check development and test dependencies against the installed Home Assistant."""

import json
import platform
import tomllib
from importlib.metadata import PackageNotFoundError, distribution, requires, version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name


def main() -> None:
    """Reject dependency drift that the package resolver cannot detect."""
    project = tomllib.loads(Path("pyproject.toml").read_text())
    groups = project["dependency-groups"]
    dev = {canonicalize_name(req.name): req for req in map(Requirement, groups["dev"])}
    test = {
        canonicalize_name(req.name): req for req in map(Requirement, groups["test"])
    }
    ha = distribution("homeassistant")
    failures = []

    if str(dev["homeassistant"].specifier) != f"=={ha.version}":
        failures.append(
            f"Home Assistant: pyproject.toml must pin installed version {ha.version}"
        )
    hacs_version = json.loads(Path("hacs.json").read_text())["homeassistant"]
    if hacs_version != ha.version:
        failures.append(
            f"hacs.json: expected Home Assistant {ha.version}, found {hacs_version}"
        )

    harness = "pytest-homeassistant-custom-component"
    if str(test[harness].specifier) != f"=={version(harness)}":
        failures.append(f"{harness}: installed version does not match the test pin")
    harness_ha = [
        req
        for req in map(Requirement, requires(harness) or [])
        if canonicalize_name(req.name) == "homeassistant"
    ]
    if len(harness_ha) != 1 or str(harness_ha[0].specifier) != f"=={ha.version}":
        failures.append(f"{harness}: must target exactly Home Assistant {ha.version}")

    for label, constraint in (
        ("Home Assistant", ha.metadata["Requires-Python"]),
        ("pyproject.toml", project["project"]["requires-python"]),
    ):
        if platform.python_version() not in SpecifierSet(constraint):
            failures.append(
                f"Python {platform.python_version()} does not satisfy {label}: {constraint}"
            )

    for component in ("bluetooth_adapters", "bluetooth", "usb"):
        manifest_path = ha.locate_file(
            f"homeassistant/components/{component}/manifest.json"
        )
        manifest = json.loads(manifest_path.read_text())
        # Check all platforms' requirements, even when running locally on macOS.
        for value in manifest.get("requirements", []):
            expected = Requirement(value)
            name = canonicalize_name(expected.name)
            declared = test.get(name)
            if declared is None or declared.specifier != expected.specifier:
                failures.append(
                    f"{component}: test dependencies must declare {expected}; found {declared}"
                )
            try:
                installed = version(name)
            except PackageNotFoundError:
                failures.append(f"{component}: {name} is not installed")
            else:
                if installed not in expected.specifier:
                    failures.append(
                        f"{component}: installed {name}=={installed} does not satisfy {expected}"
                    )

    if failures:
        raise SystemExit(
            "Home Assistant dependency compatibility failed:\n" + "\n".join(failures)
        )
    print(
        f"Dependencies match Home Assistant {ha.version} and its Bluetooth/USB manifests."
    )


if __name__ == "__main__":
    main()
