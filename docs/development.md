# Development and verification

From the repository root, create a virtual environment and install `.[dev]`,
then run `make validate PYTHON=.venv/bin/python`. This checks formatting and
core typing, builds the HACS ZIP, runs the unit/integration suite, and validates
the Compose example when Docker Compose is available. HTTP/TCP tests listen on
loopback only; they do not provision a device or call Winix.

## Packaged integration tests

The HA tests unpack the built ZIP into a temporary `custom_components/winix_local`
directory. They exercise the installed adapter and vendored core, with MQTT
mocked, and check entities, listener startup/unload and failure cleanup. They
are skipped when HA is unavailable in the development interpreter. To run in
an isolated matching HA image from the checkout, with no production mounts:

```sh
docker run --rm --network none --entrypoint python \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD:/work:ro" -w /work \
  ghcr.io/home-assistant/home-assistant:2026.10.2 \
  -B -m unittest discover -s src/tests/integration -p test_ha_runtime.py
```

Build the ZIP first. This container has no network interfaces beyond loopback;
MQTT calls are mocked and generated test bundles are temporary.

## Optional MQTT acceptance test

`test_mqtt_runtime.py` requires paho-mqtt and an explicit
`WINIX_MQTT_TEST_ROOT` containing disposable `bundle/` and `broker/` fixture
files. It connects to a test broker on loopback and checks password login, device
mTLS and topic ACLs. It is skipped by default. Use a disposable broker/network
namespace, never a production broker or real device bundle.

## Build contracts

`winix-local-build --project-root .` creates `dist/winix_local.zip` locally.
Packaging tests check deterministic output, allowed files, canonical metadata,
and independent imports from an unpacked artifact. Building does not publish.

To build the Python wheel and source distribution as well:

```sh
.venv/bin/python -m build
```

Before publishing, review the intended GitHub URLs in root `manifest.json`, the
release version and HACS requirements, provide marketplace brand assets if
seeking marketplace inclusion, and separately authorize publication. No release
upload or deployment is part of the validation workflow.
