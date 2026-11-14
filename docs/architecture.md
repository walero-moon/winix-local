# Architecture

The standalone project has three interfaces: a Python library, an installed
provisioning CLI, and a Home Assistant adapter. They share one implementation.

## Shared implementation

`src/winix_local/` contains the setup codec, validated MQTT messages, model
catalog, installation records, certificate creation, registration listener,
optional offline clock, provisioning and broker export. It imports without HA.

`catalog.json` preserves the 59 model metadata mappings exported from the previous
catalog, including sensor profiles and filter lifetimes. Data coverage does not
establish transport compatibility. The local transport retains its current
5510_US checks; this extraction does not add 5520 or other model support.

## HA adapter

`src/home_assistant/winix_local/` contains HA config flow, coordinator, entities
and lifecycle management. It imports shared modules as `._core.*`.

The build copies the required shared modules to `_core/` inside the archive.
These are generated copies; only `src/winix_local/` is maintained. The installed
HA integration is self-contained and does not depend on the checkout, CLI,
filesystem path modifications, or a separately published Python package.

The adapter uses HA's MQTT client for telemetry/control and runs local HTTP/HTTPS
registration listeners. Mosquitto remains an external broker. The build does
not start any listeners, generate credentials or connect to a purifier.

## Source and distribution layout

All Python, build utilities and tests live under `src/`. Root `manifest.json` is
canonical metadata. HACS discovers that file with `content_in_root: true` and
installs the release ZIP specified in `hacs.json`. Archive entries begin directly
with `__init__.py`, `manifest.json` and `_core/`, because HACS extracts them into
the integration's installation directory.

The Python wheel contains only `winix_local` and its catalog data. The HACS ZIP
contains only the HA adapter and required shared runtime modules. Neither
artifact contains tests, private keys or installation bundles. CI produces
artifacts for review and performs no publication or deployment.

## Readability

Separate validation, parsing, cross-field checks, record construction and side
effects with blank lines. Keep related statements together. Formatting handles
line wrapping; semantic spacing still requires review.
