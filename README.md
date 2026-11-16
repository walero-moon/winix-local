# Winix Local

[![Build](https://github.com/walero-moon/winix-local/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/walero-moon/winix-local/actions/workflows/validate.yml)
[Releases](https://github.com/walero-moon/winix-local/releases) · [Issues](https://github.com/walero-moon/winix-local/issues) · [MIT license](LICENSE)

Local registration, certificate bootstrap, MQTT communication and Home Assistant
controls for stock Winix firmware.

**Status: experimental 0.1.0.** Local registration, certificate trust, MQTT and
low/medium fan control were verified on a stock 5510_US on 2026-11-13
(it's the only hardware I have). The 59-model catalog is just metadata, not a
list of supported devices. Feel free to try it out though.

## Setup

You'll need:

- Home Assistant 2026.10.0 or newer. HA Container works too.
- An MQTT broker, already connected to Home Assistant through its
  [MQTT integration](https://www.home-assistant.io/integrations/mqtt/).
  The broker needs TLS and client-certificate authentication. Mosquitto works,
  but you can use another broker with those features.
- A computer with Wi-Fi and Python 3.9+, running Windows, macOS, or Linux.
  You'll only need it during setup. I don't plan on making a phone app.
- Obvious, but Wi-Fi for the purifier. Use a 2.4 GHz network for the 5510.

HA and the broker need to be reachable from that Wi-Fi network. They can run on
separate machines. You don't need a Winix account or any hardware modifications.

### 1. Install Winix Local

Download these two files from the [latest release](https://github.com/walero-moon/winix-local/releases/latest):

| Download | What it's for |
| --- | --- |
| `winix_local.zip` | The Home Assistant integration, ready to extract |
| `winix_local-0.1.0-py3-none-any.whl` | The setup tool, ready to install with Python |

The repository is private for now, so you'll need a GitHub account with access.
You don't need to download the source archive or clone the repository.
HACS installation will be available once the repository is public; use manual
installation for now.

Save the wheel in a folder on your setup computer and open a terminal there.
Run these commands while you have internet access. If the downloaded wheel has a
different version number, use its filename in the install command.

**macOS / Linux**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: --upgrade pip
.venv/bin/python -m pip install --only-binary=:all: ./winix_local-0.1.0-py3-none-any.whl
```

**Windows (PowerShell)**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: --upgrade pip
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: .\winix_local-0.1.0-py3-none-any.whl
```

This installs the tool and its prebuilt dependencies. You'll still need Python,
but no compiler or build tools. If a dependency has no compatible wheel, pip stops
with an error instead of trying to compile it.

Extract the contents of that ZIP into Home Assistant's
`custom_components/winix_local/` directory. In HA Container, that's
`/config/custom_components/winix_local/` inside the container. Copy the files into the
host folder mapped to that location.

Check that `manifest.json`, `config_flow.py`, and `_core/` are directly inside
`winix_local/`. Back up an existing installation before replacing its files,
then restart Home Assistant.

### 2. Find the purifier's MAC and model

Skip this if you already know the full MAC and exact model identifier.

Hold the purifier's Wi-Fi button for about three seconds until it blinks, then
connect your computer to its `WINIX SMART ...` network. On an already configured
purifier, this can reset its Wi-Fi setup.

From the folder where you installed the setup tool, run:

**macOS / Linux**

```sh
.venv/bin/python -c "from winix_local.discovery import identify_device; print(identify_device())"
```

**Windows (PowerShell)**

```powershell
.\.venv\Scripts\python.exe -c "from winix_local.discovery import identify_device; print(identify_device())"
```

The command only reads the device identity and prints the MAC and model, such as
`('AABBCCDDEEFF', '5510_US')`. If automatic MAC lookup fails and
you know the full MAC, replace `identify_device()` with
`identify_device(mac='AABBCCDDEEFF')`, using your own address.

Write down both values, then reconnect your computer to your usual network.
You'll need the full MAC, not the short suffix in the Wi-Fi name or the FCC ID.

### 3. Add the integration in Home Assistant

Go to **Settings → Devices & services → Add integration → Winix Local**.
Choose **Set up certificates and local endpoints** and enter:

| Field | What to enter |
| --- | --- |
| MAC and model | The values from step 2, including the model suffix, such as `_US` |
| Registration host | The LAN address or hostname of your Home Assistant server |
| HTTP / HTTPS ports | Leave these at `18080` and `18443` unless you need different ports |
| MQTT host | The LAN address or hostname of your broker |
| MQTT TLS port | Usually `8883`; it must match your broker's device listener |

Use stable addresses, without `http://`, `https://`, or a port in the hostname.
The purifier needs to reach these addresses itself, so don't use `localhost`.

If HA runs in a container, publish the two registration ports as well. Entering
them in this form won't change Docker's port mappings. If you use VLANs, allow
the purifier and setup computer to reach those ports and the broker's TLS port.
HA's own MQTT connection can keep using its existing broker listener.

Choose how to create the certificates:

- **Generate a dedicated Winix CA and certificates** is the easiest option.
- **Issue certificates with my CA and signing key** uses a CA you already own.
- **Import already-issued certificates** uses certificate/key pairs you've
  prepared yourself. See [certificate imports](#certificate-imports) below.

**Retain the CA signing key on HA** is off by default. HA uses the key to create
the certificates and only saves it if you enable this option. You can use a saved
key to issue certificates later; this version doesn't renew them automatically.
A retained key lives in HA's configuration folder and may be included in backups. Saved keys aren't encrypted at rest, so keep those backups
private.

Review the settings and submit. If you already have an installation bundle in
`/config/winix_local/<MAC>/`, use **Use an existing installation bundle** instead.
Existing bundles won't be overwritten.

### 4. Configure your broker

Open the Winix Local entry's **Configure** dialog and download:

- The broker certificate package, containing `ca.pem`, `broker.pem`,
  `broker.key`, and `requirements.json`.
- The public provisioning configuration, `provision.json`, for the setup tool.

In your broker's settings:

1. Install `broker.pem` and `broker.key` as the TLS listener's server identity.
2. Trust `ca.pem` for client authentication and require a valid client certificate.
3. Map the verified client certificate's common name to the device identity.
4. Apply the publish/subscribe permissions listed in `requirements.json` for the
   purifier and HA's MQTT user.

Use the exact topics in that file and keep the broker's other users and settings
in place. You'll need to apply these changes yourself.

The ZIP contains the broker's private key, so keep it private. The CA signing key
and the purifier's private key aren't included. The download links expire after
five minutes; reopen Configure for new ones. Anyone with a link can use it until
it expires or its issuing login is revoked, so don't share the links either.

For broker installation, follow your broker's own instructions. For Mosquitto
settings, see the
[configuration reference](https://mosquitto.org/man/mosquitto-conf-5.html).
The [Compose example](examples/mosquitto/compose.yaml) is optional; the HA download
isn't a complete Mosquitto configuration you can mount and run as-is.

### 5. Connect the purifier

Save `provision.json` on your computer, for example in Downloads. Start with the
computer on your **usual network**, where it can reach HA and the broker. Run
the command from the folder where you installed the setup tool.

**macOS / Linux**

```sh
.venv/bin/python -m winix_local setup --config "$HOME/Downloads/provision.json"
```

**Windows (PowerShell)**

```powershell
.\.venv\Scripts\python.exe -m winix_local setup --config "$HOME\Downloads\provision.json"
```

Change the path if you saved the file somewhere else.

The tool checks the local servers first, then asks you to put the purifier in
pairing mode and join its `WINIX SMART ...` network. Follow the prompts to choose
the purifier's target Wi-Fi and enter its password. Review the settings when
prompted, then confirm to send them.

If the Wi-Fi scan fails, you can enter the network manually. You'll need its name
and the access point's BSSID, which is different from the purifier's MAC.

After sending the settings, reconnect your computer to your usual network.
You don't need USB tethering or a second adapter for this process. Keep `--config`
in the command so the tool uses the certificates you just created in HA.

### 6. Check that it works

Look for fresh readings in HA, then change the fan speed and check that the
purifier responds. A "settings sent" message alone doesn't confirm a connection.

Restart the purifier without pressing its Wi-Fi button and check that it
reconnects. A long press on Wi-Fi puts it back into pairing mode.
HA and the broker stay running; the setup computer can be disconnected.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Winix Local doesn't appear in HA | ZIP extracted into the right folder, HA restarted, and HA version is supported |
| HA entry keeps retrying | HA's MQTT connection, registration port mappings, port conflicts, and HA logs |
| Setup tool can't reach the servers | Computer is on its usual network, listeners are running, and routing/firewall rules allow access |
| TLS or CA fingerprint error | Correct `provision.json`, certificate hostname, and device clock; don't turn off certificate verification |
| Can't identify the purifier | Correct hotspot and pairing mode; Linux needs `ip` or `arp`, Windows needs `arp.exe`; try the full MAC fallback |
| Purifier joins Wi-Fi but stays unavailable | Its routes to HA and MQTT, DNS/NTP, broker client-certificate settings, CN mapping, and topic permissions |
| Pip reports no matching binary distribution | Update pip and use a Python/platform combination with dependency wheels; CI checks Python 3.9 and 3.13 on Windows, macOS and Linux |
| Download link stopped working | Reopen Configure while signed in as an HA administrator |

If the entry is retrying, you can still open Configure to download its files.
Fix the broker connection or registration listener, then reload the entry before
running the setup tool.

Back up a working `/config/winix_local/<MAC>/` directory before replacing anything.
If setup stopped after creating that directory, use the existing-bundle option
instead of generating new credentials. The UI doesn't currently edit an existing
installation's hosts or certificates in place. Replacing its CA requires setting
up the purifier again.

### Blocking internet access

You can block the purifier's internet access. It talks to HA and your broker
locally, but still needs DNS and NTP to set its clock for TLS. If you block all
internet access, provide those services locally or redirect them on your network,
while allowing
the routes to HA and the broker. The setup tool doesn't configure firewall,
DHCP, DNS, or NTP settings.

### More than one purifier

Give each HA entry its own registration HTTP/HTTPS ports. Devices sharing a broker
TLS listener must trust its server certificate, so use a common imported CA or
separate broker listeners for different CAs. Generating a fresh CA for a second
device doesn't change what the first device trusts.

### Certificate imports

The import form accepts one RSA CA directly signing the registration-server,
broker-server, and purifier-client certificates. Keys must be at least 2048 bits,
and certificates must be currently valid.

Server certificates need server-auth usage and a SAN matching the exact configured
hostname or IP. The purifier certificate needs client-auth usage and its common
name must be the uppercase MAC without separators. Paste PEM certificate/key pairs;
the form checks that they match.

Intermediate chains, EC keys, encrypted PEM keys, wildcard-only hostname SANs, and
separate CAs for registration and MQTT aren't supported by this form yet. Use HTTPS
when pasting private keys into HA. Prefer a dedicated Winix CA over importing a
CA key used throughout your home. The broker never needs the CA signing key.

If you discard a generated CA key, you can't recover it to sign new certificates.
Keep your original backup when importing your own key. Neither option currently
enables automatic renewal.

## Development

### Run the checks

From the repository root:

**macOS / Linux**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
make validate PYTHON=.venv/bin/python
```

**Windows (PowerShell)**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m black --check src
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m winix_local.build --project-root .
.\.venv\Scripts\python.exe -B -m unittest discover -s src/tests -t src -p 'test_*.py'
```

`make validate` checks formatting, types, the built ZIP, tests, and the Compose
example when Docker Compose is available. The HA runtime tests run only when HA is
installed; the ordinary Python suite skips them otherwise.

The suite has passed on macOS and isolated Linux/HA, including the setup forms,
certificate checks and authenticated downloads. The new UI still needs a full
setup and restart/reconnection test with a real purifier. The platform test matrix
runs on GitHub Actions; setting up a real purifier from Windows still needs a
manual check. The 5520 and other catalog models haven't been physically verified.

### Build packages

The release workflow builds the downloads for users. To build them locally:

**macOS / Linux**

```sh
.venv/bin/python -m build
.venv/bin/python -m winix_local.build --project-root .
```

**Windows (PowerShell)**

```powershell
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m winix_local.build --project-root .
```

The wheel and source archive are written to `dist/`, alongside the HA ZIP.

### Repository layout

```text
src/
  winix_local/                  Shared library and setup/build tools
  home_assistant/winix_local/    HA integration
  tests/
    unit/
    integration/
  tools/                        Release installation checks
examples/mosquitto/              Optional Compose example
.github/workflows/              CI checks
```

All Python code and tests live under `src/`. The ZIP builder copies shared modules
into the integration's `_core/` package; edit their source in `src/winix_local/`,
not the generated copies. Installing the raw HA source folder won't work without
that build step.

`manifest.json` is the shared HA metadata. `hacs.json` describes the ZIP release
layout. Each tagged release includes `winix_local.zip`. While the repository is
private, download it with an account that has access and install it manually.
Making the repository public and listing it in HACS are separate steps.

The installed commands are `winix-local`, `winix-local-broker` and
`winix-local-build`. The broker exporter writes Mosquitto-specific files from an
existing private bundle; it is separate from the HA UI's generic certificate ZIP.

### Contributing and releases

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and release instructions,
[CHANGELOG.md](CHANGELOG.md) for release notes, and [SECURITY.md](SECURITY.md) for
security reports. The project uses the [MIT license](LICENSE).

CI checks Python 3.9 and 3.13 on Windows, macOS and Linux, plus the built integration
inside HA. The [Install release workflow](https://github.com/walero-moon/winix-local/actions/workflows/install.yml)
also installs the built wheel in fresh environments on those platforms, using
only prebuilt dependencies. Future version tags must pass those installation
checks before the release assets are published.
