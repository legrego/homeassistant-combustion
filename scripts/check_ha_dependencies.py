"""Check development and test dependencies against the installed Home Assistant."""

import ast
import json
import platform
import tomllib
from importlib.metadata import (
    Distribution,
    PackageNotFoundError,
    distribution,
    requires,
    version,
)
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

HARNESS = "pytest-homeassistant-custom-component"
COMPONENTS = ("bluetooth_adapters", "bluetooth", "usb")

Group = dict[str, Requirement]


def component_requirements(ha: Distribution) -> list[tuple[str, Requirement]]:
    """Return what Home Assistant installs for each component the tests need."""
    result = []
    for component in COMPONENTS:
        manifest = json.loads(
            Path(
                ha.locate_file(f"homeassistant/components/{component}/manifest.json")
            ).read_text()
        )
        # Check all platforms' requirements, even when running locally on macOS.
        result.extend(
            (component, Requirement(value))
            for value in manifest.get("requirements", [])
        )
    return result


def check_config_requirements(ha: Distribution) -> list[tuple[str, Requirement]]:
    """Return what `hass --script check_config` installs when it is missing."""
    path = Path(ha.locate_file("homeassistant/scripts/check_config.py"))
    # Read the constant without importing all of Home Assistant.
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "REQUIREMENTS"
            for target in node.targets
        ):
            return [
                ("check_config", Requirement(value))
                for value in ast.literal_eval(node.value)
            ]
    return []


def check_versions(project: dict, dev: Group, test: Group, ha: Distribution) -> list:
    """Check the Home Assistant, test harness, HACS, and Python versions."""
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

    if str(test[HARNESS].specifier) != f"=={version(HARNESS)}":
        failures.append(f"{HARNESS}: installed version does not match the test pin")
    harness_ha = [
        req
        for req in map(Requirement, requires(HARNESS) or [])
        if canonicalize_name(req.name) == "homeassistant"
    ]
    if len(harness_ha) != 1 or str(harness_ha[0].specifier) != f"=={ha.version}":
        failures.append(f"{HARNESS}: must target exactly Home Assistant {ha.version}")

    for label, constraint in (
        ("Home Assistant", ha.metadata["Requires-Python"]),
        ("pyproject.toml", project["project"]["requires-python"]),
    ):
        if platform.python_version() not in SpecifierSet(constraint):
            failures.append(
                f"Python {platform.python_version()} does not satisfy {label}: {constraint}"
            )
    return failures


def check_requirement(
    source: str, expected: Requirement, group: str, declared: Requirement | None
) -> list:
    """Check that a requirement is declared as Home Assistant does, and installed."""
    failures = []
    # Compare extras and markers too, which change what is installed and where.
    if declared != expected:
        failures.append(
            f"{source}: {group} dependencies must declare {expected}; found {declared}"
        )
    try:
        installed = version(expected.name)
    except PackageNotFoundError:
        failures.append(f"{source}: {expected.name} is not installed")
    else:
        if installed not in expected.specifier:
            failures.append(
                f"{source}: installed {expected.name}=={installed} does not satisfy {expected}"
            )
    return failures


def renovate_exclusions() -> set[str]:
    """Return the packages that Renovate never updates independently."""
    rules = json.loads(Path("renovate.json").read_text())["packageRules"]
    return {
        canonicalize_name(name)
        for rule in rules
        # Rules with other matchers, such as matchUpdateTypes, only exclude some updates.
        if set(rule) <= {"description", "matchPackageNames", "enabled"}
        and rule.get("enabled") is False
        for name in rule.get("matchPackageNames", [])
    }


def main() -> None:
    """Reject dependency drift that the package resolver cannot detect."""
    project = tomllib.loads(Path("pyproject.toml").read_text())
    dev, test = (
        {
            canonicalize_name(req.name): req
            for req in map(Requirement, project["dependency-groups"][group])
        }
        for group in ("dev", "test")
    )
    ha = distribution("homeassistant")
    failures = check_versions(project, dev, test, ha)

    components = component_requirements(ha)
    check_config = check_config_requirements(ha)
    for group, pins, required in (
        ("test", test, components),
        ("dev", dev, check_config),
    ):
        for source, expected in required:
            declared = pins.get(canonicalize_name(expected.name))
            failures.extend(check_requirement(source, expected, group, declared))

    # Only the test group is limited to what Home Assistant requires.
    required_names = {canonicalize_name(req.name) for _, req in components}
    failures.extend(
        f"test dependencies: {name} is not required by {', '.join(COMPONENTS)}"
        for name in sorted(test.keys() - required_names - {HARNESS})
    )

    controlled = required_names | {
        canonicalize_name(req.name) for _, req in check_config
    }
    failures.extend(
        f"renovate.json: must not update {name} independently of Home Assistant"
        for name in sorted(controlled - renovate_exclusions())
    )

    if failures:
        raise SystemExit(
            "Home Assistant dependency compatibility failed:\n" + "\n".join(failures)
        )
    print(
        f"Dependencies match Home Assistant {ha.version} and what it requires of them."
    )


if __name__ == "__main__":
    main()
