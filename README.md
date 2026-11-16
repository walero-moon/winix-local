# Winix Local

Local registration, certificate bootstrap, MQTT communication and Home Assistant
controls for stock Winix firmware.

**Status: experimental 0.1.0.** Local registration, certificate trust, MQTT and
low/medium fan control were verified on a stock 5510_US on 2026-11-13
(it's the only hardware I have). The 59-model catalog is just metadata, not a
list of supported devices. Feel free to try it out though.

## Setup

Start with the [Home Assistant setup guide](docs/ha-setup.md). It covers installing
the built integration, generating or importing certificates in HA, configuring
your existing MQTT broker, and running the guided Wi-Fi provisioning tool.

You need Home Assistant 2026.10.0 or newer, a compatible MQTT broker reachable
from HA and the purifier, and a Windows, macOS, or Linux computer with Wi-Fi for
one-time provisioning. The broker and HA stay running afterward; the setup
computer does not.

This is a local development checkout. A published HACS release is not yet part
of these instructions; build the ZIP and install it manually as described in the
guide. The new setup UI has automated HA tests, but its complete real-device
walkthrough and native Windows verification are still pending.

## Repository layout

```text
src/
  winix_local/                  Shared library, provisioning CLI and ZIP builder
  home_assistant/winix_local/    HA adapter source; imports bundled _core modules
  tests/
    unit/                       Catalog, wire, codec and packaging checks
    integration/                TCP provisioning, HTTPS registration and HA tests
docs/                           HA installation and setup guide
examples/mosquitto/              Portable Compose configuration
.github/workflows/              Validation and build-artifact configuration
```

All authored Python and tests live under `src/`. The repository root contains
project metadata, documentation and development configuration. `manifest.json`
is canonical HA metadata; HACS reads it from the root because `hacs.json` enables
`content_in_root`. Both files are also checked by the ZIP builder.

## Development

Python 3.9+ is required on Windows, macOS, and Linux. From the repository root:

macOS/Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
make validate PYTHON=.venv/bin/python
```

Windows (PowerShell):

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m black --check src
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m winix_local.build --project-root .
.\.venv\Scripts\python.exe -B -m unittest discover -s src/tests -t src -p 'test_*.py'
```

To run the guided setup on Windows:

```powershell
.\.venv\Scripts\winix-local.exe setup
```

The installed commands are `winix-local` (installation/provisioning),
`winix-local-broker` (broker configuration export), and `winix-local-build`.
`python -m winix_local` also runs the provisioning command.

## Home Assistant package

```sh
.venv/bin/winix-local-build --project-root .
```

On Windows:

```powershell
.\.venv\Scripts\winix-local-build.exe --project-root .
```

This creates `dist/winix_local.zip`. Its contents install directly into
`/config/custom_components/winix_local/`. The build bundles the shared modules
into `_core/` from their single source. It excludes CLI/build code, tests,
installation bundles and private configuration. HA needs no separate Winix
package installation or PyPI release. No certificate or device setting changes
occur during a build.

Install the **built ZIP**, following [installation and setup instructions](docs/ha-setup.md).
The raw `src/home_assistant/` tree needs the build-generated `_core/` directory
and is not an installation artifact on its own.
