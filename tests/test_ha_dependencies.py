"""Exercise the dependency guard against a synthetic project and Home Assistant.

The versions here are made up, so that upgrading Home Assistant does not change them.
"""

import json
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import check_ha_dependencies

ROOT = Path(__file__).resolve().parents[1]
HARNESS = "pytest-homeassistant-custom-component"


class FakeProject:
    """A project directory and the packages installed for it, which agree by default."""

    def __init__(self, path: Path) -> None:
        """Initialize."""
        self.path = path
        self.requires_python = ">=3.50.2,<3.51"
        self.dev = ["homeassistant==2050.1.2", "script-lib==7.0", "unrelated==1.0"]
        self.test = [f"{HARNESS}==0.1.2", "radio==1.0", "adapters==2.0", "serial==3.0"]
        self.hacs = "2050.1.2"
        self.renovate_rules = [
            {
                "description": "Anything",
                "matchPackageNames": ["radio", "adapters", "serial", "script-lib"],
                "enabled": False,
            }
        ]
        self.python = "3.50.4"
        self.ha_requires_python = ">=3.50.1"
        self.harness_requires = ["homeassistant==2050.1.2", "pytest==1.0"]
        self.installed = {
            "homeassistant": "2050.1.2",
            HARNESS: "0.1.2",
            "radio": "1.0",
            "adapters": "2.0",
            "serial": "3.0",
            "script-lib": "7.0",
        }
        self.manifests = {
            "bluetooth_adapters": ["adapters==2.0"],
            "bluetooth": ["radio==1.0"],
            "usb": ["serial==3.0"],
        }
        self.check_config = 'REQUIREMENTS = ("script-lib==7.0",)'

    def version(self, name: str) -> str:
        """Stand in for importlib.metadata.version."""
        if name not in self.installed:
            raise PackageNotFoundError(name)
        return self.installed[name]

    def run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Write the project and Home Assistant to disk, then run the guard."""
        groups = f"dev = {json.dumps(self.dev)}\ntest = {json.dumps(self.test)}\n"
        site_packages = self.path / "site-packages"
        files = {
            "pyproject.toml": f'[project]\nrequires-python = "{self.requires_python}"\n'
            f"[dependency-groups]\n{groups}",
            "hacs.json": json.dumps({"homeassistant": self.hacs}),
            "renovate.json": json.dumps({"packageRules": self.renovate_rules}),
            "site-packages/homeassistant/scripts/check_config.py": self.check_config,
        }
        for component, requirements in self.manifests.items():
            manifest = {} if requirements is None else {"requirements": requirements}
            files[
                f"site-packages/homeassistant/components/{component}/manifest.json"
            ] = json.dumps(manifest)
        for name, contents in files.items():
            (self.path / name).parent.mkdir(parents=True, exist_ok=True)
            (self.path / name).write_text(contents)

        ha = SimpleNamespace(
            version=self.installed["homeassistant"],
            metadata={"Requires-Python": self.ha_requires_python},
            locate_file=site_packages.joinpath,
        )
        monkeypatch.chdir(self.path)
        for name, replacement in (
            ("distribution", {"homeassistant": ha}.__getitem__),
            ("requires", {HARNESS: self.harness_requires}.__getitem__),
            ("version", self.version),
            ("platform", SimpleNamespace(python_version=lambda: self.python)),
        ):
            monkeypatch.setattr(check_ha_dependencies, name, replacement)
        check_ha_dependencies.main()


@pytest.fixture
def project(tmp_path: Path) -> FakeProject:
    """Return a project that matches Home Assistant."""
    return FakeProject(tmp_path)


def test_accepts_matching_dependencies(project, monkeypatch, capsys):
    """Accept a project that matches Home Assistant."""
    project.run(monkeypatch)
    assert "Dependencies match Home Assistant 2050.1.2" in capsys.readouterr().out


def test_accepts_equivalent_names_and_absent_requirements(project, monkeypatch):
    """Accept other spellings of a name, and manifests without requirements."""
    project.test.remove("serial==3.0")
    project.test[project.test.index("radio==1.0")] = "Radio==1.0"
    project.manifests["usb"] = None
    project.check_config = "OTHER = 1"
    project.run(monkeypatch)


@pytest.mark.parametrize(
    ("changes", "errors"),
    [
        pytest.param(
            {"dev": ["homeassistant==2050.1.1", "script-lib==7.0"]},
            ["Home Assistant: pyproject.toml must pin installed version 2050.1.2"],
            id="home-assistant-pin",
        ),
        pytest.param(
            {"hacs": "2050.1.1"},
            ["hacs.json: expected Home Assistant 2050.1.2, found 2050.1.1"],
            id="hacs",
        ),
        pytest.param(
            {
                "test": [
                    f"{HARNESS}==0.1.1",
                    "radio==1.0",
                    "adapters==2.0",
                    "serial==3.0",
                ]
            },
            [f"{HARNESS}: installed version does not match the test pin"],
            id="harness-pin",
        ),
        *(
            pytest.param(
                {"harness_requires": harness_requires},
                [f"{HARNESS}: must target exactly Home Assistant 2050.1.2"],
                id=f"harness-target-{name}",
            )
            for name, harness_requires in (
                ("other", ["homeassistant==2050.1.1"]),
                ("loose", ["homeassistant>=2050.1.2"]),
                ("absent", ["pytest==1.0"]),
                ("no-requirements", None),
            )
        ),
        pytest.param(
            {"ha_requires_python": ">=3.51"},
            ["Python 3.50.4 does not satisfy Home Assistant: >=3.51"],
            id="home-assistant-python",
        ),
        pytest.param(
            {"requires_python": ">=3.51,<3.52"},
            ["Python 3.50.4 does not satisfy pyproject.toml: >=3.51,<3.52"],
            id="project-python",
        ),
        *(
            pytest.param(
                {
                    "manifests": {"bluetooth": [], "usb": [], "bluetooth_adapters": []}
                    | {component: ["new==1.0"]}
                },
                [
                    f"{component}: test dependencies must declare new==1.0; found None",
                    f"{component}: new is not installed",
                    "renovate.json: must not update new independently",
                ],
                id=f"undeclared-{component}",
            )
            for component in ("bluetooth_adapters", "bluetooth", "usb")
        ),
        *(
            pytest.param(
                {"test": [f"{HARNESS}==0.1.2", pin, "adapters==2.0", "serial==3.0"]},
                [
                    f"bluetooth: test dependencies must declare radio==1.0; found {found}"
                ],
                id=f"declared-{name}",
            )
            for name, pin, found in (
                ("other-version", "radio==1.1", "radio==1.1"),
                ("loose", "radio>=1.0", "radio>=1.0"),
                ("extra", "radio[fast]==1.0", "radio[fast]==1.0"),
                (
                    "marker",
                    "radio==1.0; os_name == 'nt'",
                    'radio==1.0; os_name == "nt"',
                ),
            )
        ),
        pytest.param(
            {"check_config": 'REQUIREMENTS = ("script-lib==7.1",)'},
            [
                "check_config: dev dependencies must declare script-lib==7.1; found script-lib==7.0",
                "check_config: installed script-lib==7.0 does not satisfy script-lib==7.1",
            ],
            id="check-config",
        ),
        pytest.param(
            {
                "manifests": {
                    "bluetooth_adapters": ["adapters==2.0"],
                    "bluetooth": ["radio==1.0"],
                    "usb": [],
                }
            },
            ["test dependencies: serial is not required by"],
            id="stale-pin",
        ),
        pytest.param(
            {"renovate_rules": []},
            [
                f"renovate.json: must not update {name} independently"
                for name in ("adapters", "radio", "script-lib", "serial")
            ],
            id="renovate-no-rule",
        ),
    ],
)
def test_rejects_drift(project, monkeypatch, changes, errors):
    """Reject drift, omissions, loosened constraints, and stale pins."""
    for name, value in changes.items():
        setattr(project, name, value)
    with pytest.raises(SystemExit) as exc_info:
        project.run(monkeypatch)
    for error in errors:
        assert error in str(exc_info.value)


def test_rejects_other_installed_version(project, monkeypatch):
    """Reject an installed package that differs from a correct pin."""
    project.installed["radio"] = "1.1"
    with pytest.raises(SystemExit, match="installed radio==1.1 does not satisfy radio"):
        project.run(monkeypatch)


@pytest.mark.parametrize(
    "change",
    [{"enabled": True}, {"matchUpdateTypes": ["major"]}, {"matchPackageNames": []}],
)
def test_rejects_partial_renovate_exclusion(project, monkeypatch, change):
    """Reject rules that still allow some independent updates."""
    project.renovate_rules[0] |= change
    with pytest.raises(SystemExit, match="must not update radio independently"):
        project.run(monkeypatch)


def test_this_project():
    """Accept this project with the Home Assistant that is installed."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_ha_dependencies.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
