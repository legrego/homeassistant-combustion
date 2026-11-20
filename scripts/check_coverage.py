"""Enforce Bronze config flow coverage and Silver coverage for every module."""

import json
from pathlib import Path


def main() -> None:
    """Check the line and branch coverage report produced by pytest."""
    report = json.loads(Path("test_results/cov_json/coverage.json").read_text())
    if not report["meta"]["branch_coverage"]:
        raise SystemExit("Coverage must include branches.")

    # Require every source module, including any that were never imported by tests.
    modules = sorted(Path("custom_components/combustion").rglob("*.py"))
    failures = []
    for module in modules:
        if (result := report["files"].get(module.as_posix())) is None:
            failures.append(f"{module}: missing coverage")
            continue
        summary = result["summary"]
        covered = summary["covered_lines"] + summary["covered_branches"]
        total = summary["num_statements"] + summary["num_branches"]
        if module.name == "config_flow.py":
            if covered != total:
                failures.append(f"{module}: requires 100% line and branch coverage")
        elif total and covered * 100 <= total * 95:
            failures.append(f"{module}: requires more than 95% coverage")

    if failures:
        raise SystemExit("\n".join(failures))
    print("Every module exceeds 95% coverage; config_flow.py has 100% coverage.")


if __name__ == "__main__":
    main()
