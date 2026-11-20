# Contribution guidelines

Contributing to this project should be as easy and transparent as possible, whether it's:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing new features

## Github is used for everything

Github is used to host code, to track issues and feature requests, as well as accept pull requests.

Pull requests are the best way to propose changes to the codebase.

1. Fork the repo and create your branch from `main`.
2. If you've changed something, update the documentation.
3. Make sure your code lints (using `scripts/lint`).
4. Test you contribution.
5. Issue that pull request!

## Any contributions you make will be under the MIT Software License

In short, when you submit code changes, your submissions are understood to be under the same [MIT License](http://choosealicense.com/licenses/mit/) that covers the project. Feel free to contact the maintainers if that's a concern.

## Report bugs using Github's [issues](../../issues)

GitHub issues are used to track public bugs.
Report a bug by [opening a new issue](../../issues/new/choose); it's that easy!

## Write bug reports with detail, background, and sample code

**Great Bug Reports** tend to have:

- A quick summary and/or background
- Steps to reproduce
  - Be specific!
  - Give sample code if you can.
- What you expected would happen
- What actually happens
- Notes (possibly including why you think this might be happening, or stuff you tried that didn't work)

People *love* thorough bug reports. I'm not even kidding.

## Use a Consistent Coding Style

Use the configured linter to check your code, and make sure it follows the project conventions.

## Local development environment

Visual Studio Code is the recommended code editor for this project.
This project includes a [devcontainer](./.devcontainer) configuration for an easy to use and consistent development environment. With this container you will have a stand alone Home Assistant instance running and already configured with the included [`configuration.yaml`](./config/configuration.yaml) file.

### Dependency management
Dependencies are managed with [Poetry](https://python-poetry.org) 2.2 or newer, and need Python 3.14. The dev container sets this up for you. Outside the dev container, install Poetry and the dependencies yourself:

```sh
pipx install poetry
poetry install
```

The Home Assistant version in `pyproject.toml` must match the `homeassistant` key in `hacs.json`, and the `combustion_ble` version must match `requirements` in `custom_components/combustion/manifest.json`.

### Running tests
Use `./scripts/test` to run the tests. Arguments are passed to pytest:

```sh
poetry run ./scripts/test
```

### Linting
Use `./scripts/lint` to format the code and fix lint errors:

```sh
poetry run ./scripts/lint
```

### Running Home Assistant
Use `./scripts/develop` to start Home Assistant with the integration and the configuration in [`config/configuration.yaml`](./config/configuration.yaml):

```sh
poetry run ./scripts/develop
```

## License

By contributing, you agree that your contributions will be licensed under its MIT License.