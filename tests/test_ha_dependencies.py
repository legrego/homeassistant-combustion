"""Exercise the dependency guard against real installed Home Assistant metadata."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts/check_ha_dependencies.py"


@pytest.mark.parametrize(
    ("filename", "old", "new", "error"),
    [
        (
            "pyproject.toml",
            "habluetooth==7.1.2",
            "habluetooth==7.1.3",
            "habluetooth==7.1.2",
        ),
        (
            "pyproject.toml",
            "dbus-fast==5.0.22",
            "dbus-fast==5.2.0",
            "dbus-fast==5.0.22",
        ),
        ("pyproject.toml", '    "bleak==3.0.2",\n', "", "bleak==3.0.2; found None"),
        ("pyproject.toml", "bleak==3.0.2", "bleak>=3.0.2", "must declare bleak==3.0.2"),
        ("hacs.json", "2026.10.4", "2026.10.3", "hacs.json: expected Home Assistant"),
        (
            "pyproject.toml",
            "pytest-homeassistant-custom-component==0.13.375",
            "pytest-homeassistant-custom-component==0.13.374",
            "installed version does not match the test pin",
        ),
        (
            "pyproject.toml",
            ">=3.14.2,<3.15",
            ">=3.15,<3.16",
            "does not satisfy pyproject.toml",
        ),
        ("pyproject.toml", "", "", ""),
    ],
)
def test_dependency_compatibility(tmp_path, filename, old, new, error):
    """Accept aligned pins and reject drift, omissions, and loosened constraints."""
    for name in ("pyproject.toml", "hacs.json"):
        contents = (ROOT / name).read_text()
        if name == filename and old:
            assert old in contents
            contents = contents.replace(old, new)
        (tmp_path / name).write_text(contents)

    result = subprocess.run(
        [sys.executable, str(CHECK)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if error:
        assert result.returncode != 0
        assert error in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert "Dependencies match Home Assistant" in result.stdout
