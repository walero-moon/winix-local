# Winix Local

Local registration, certificate bootstrap, MQTT communication and Home Assistant
controls for stock Winix firmware. A laptop provisions the purifier once; the
HA integration serves registration and uses your existing MQTT broker.

**Status: experimental 0.1.0.** Local registration, certificate trust, MQTT and
low/medium fan control were verified on a stock 5510_US on 2026-11-13. Power,
auto/manual mode and PlasmaWave controls are implemented but have not all been
physically exercised. Provisioning and transport still accept only 5510_US.
The 59-model catalog is metadata, not a supported-device list.

## Repository layout

```text
src/
  winix_local/                  Shared library, provisioning CLI and ZIP builder
  home_assistant/winix_local/    HA adapter source; imports bundled _core modules
  tests/
    unit/                       Catalog, wire, codec and packaging checks
    integration/                TCP provisioning, HTTPS registration and HA tests
docs/                           Installation, architecture and development
examples/mosquitto/              Portable Compose configuration
.github/workflows/              Validation and build-artifact configuration
```

All authored Python and tests live under `src/`. The repository root contains
project metadata, documentation and development configuration. `manifest.json`
is canonical HA metadata; HACS reads it from the root because `hacs.json` enables
`content_in_root`. Both files are also checked by the ZIP builder.

## Development

Python 3.9+ and `/usr/bin/openssl` are required. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
make validate PYTHON=.venv/bin/python
```

The installed commands are `winix-local` (installation/provisioning),
`winix-local-broker` (broker configuration export), and `winix-local-build`.
`python -m winix_local` also runs the provisioning command.

## Home Assistant package

```sh
.venv/bin/winix-local-build --project-root .
```

This creates `dist/winix_local.zip`. Its contents install directly into
`/config/custom_components/winix_local/`. The build bundles the shared modules
into `_core/` from their single source. It excludes CLI/build code, tests,
installation bundles and private configuration. HA needs no separate Winix
package installation or PyPI release. No certificate or device setting changes
occur during a build.

Install the **built ZIP**, following [installation instructions](docs/installation.md).
The raw `src/home_assistant/` tree needs the build-generated `_core/` directory
and is not an installation artifact on its own.

## Publication status

This is a local repository and build. No GitHub repository, release, PyPI package,
HACS listing or live installation has been created by this extraction. HACS ZIP
mode needs a public repository and a release asset named `winix_local.zip` before
users can install through HACS. The manifest's GitHub URLs identify the intended
repository location and must be reviewed before publication. Marketplace brand
assets and marketplace acceptance are separate release preparation.

The GitHub workflow validates and uploads CI artifacts when explicitly run on
GitHub; it has no release, package-publication or deployment step.

## Documentation

- [Installation and existing-broker setup](docs/installation.md)
- [Architecture and packaging](docs/architecture.md)
- [Development and verification](docs/development.md)

This project was extracted from the Homelab prototype at commit `3f45bd2`.
Host-specific networking, Ansible, deployment state and historical lab captures
remain in Homelab. No host-specific files or private installation material are
required to build or test this repository.

Project code is distributed under the included MIT license.

---

Inspired by [iprak/winix](https://github.com/iprak/winix).
