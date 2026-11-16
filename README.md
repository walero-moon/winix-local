# Winix Local

Local registration, certificate bootstrap, MQTT communication and Home Assistant
controls for stock Winix firmware.

**Status: experimental 0.1.0.** Local registration, certificate trust, MQTT and
low/medium fan control were verified on a stock 5510_US on 2026-11-13
(it's the only hardware I have). The 59-model catalog is just metadata, not a
list of supported devices. Feel free to try it out though.

## Setup

You'll need:

- **Home Assistant 2026.10.0 or newer.** HA Container works too.
- **An MQTT broker**, already connected to Home Assistant through its
  [MQTT integration](https://www.home-assistant.io/integrations/mqtt/).
  The broker needs TLS and client-certificate authentication. Mosquitto works,
  but you can use another broker with those features.
- **A computer with Wi-Fi and Python 3.9+**, running Windows, macOS, or Linux.
  You'll only need it during setup.
- **Wi-Fi for the purifier.** Use a 2.4 GHz network for the 5510.

HA and the broker need to be reachable from that Wi-Fi network. They can run on
separate machines. You don't need a Winix account or any hardware modifications.

### 1. Install Winix Local

For now, install from source. Download this repository onto your computer and
open a terminal in its folder. Run these commands while you still have internet
access:

**macOS / Linux**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
.venv/bin/python -m winix_local.build --project-root .
```

**Windows — PowerShell**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m winix_local.build --project-root .
```

This gives you the setup tool and `dist/winix_local.zip`.

Extract the **contents** of that ZIP into Home Assistant's
`custom_components/winix_local/` directory. In HA Container, that's
`/config/custom_components/winix_local/` inside the container. Use the corresponding
configuration folder on the host when copying files.

You should end up with `manifest.json`, `config_flow.py`, and `_core/` directly
inside `winix_local/`. Back up an existing installation before replacing its files,
then restart Home Assistant.

### 2. Find the purifier's MAC and model

Skip this if you already know the full MAC and exact model identifier.

Hold the purifier's Wi-Fi button for about three seconds until it blinks, then
connect your computer to its `WINIX SMART ...` network. On an already configured
purifier, this can reset its Wi-Fi setup.

Run the following from the same project folder:

**macOS / Linux**

```sh
.venv/bin/python -c "from winix_local.discovery import identify_device; print(identify_device())"
```

**Windows — PowerShell**

```powershell
.\.venv\Scripts\python.exe -c "from winix_local.discovery import identify_device; print(identify_device())"
```

It will print the MAC and model, such as `('AABBCCDDEEFF', '5510_US')`.
This command only reads the device identity. If automatic MAC lookup fails and
you know the full MAC, replace `identify_device()` with
`identify_device(mac='AABBCCDDEEFF')`, using your own address.

Write down both values, then reconnect your computer to your usual network.
The short suffix in the Wi-Fi name isn't the full MAC, and the FCC ID isn't a MAC.

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
Don't use `localhost`—the purifier needs to reach these addresses itself.

If HA runs in a container, publish the two registration ports as well. Entering
them in this form won't change Docker's port mappings. If you use VLANs, allow
the purifier and setup computer to reach those ports and the broker's TLS port.
HA's own MQTT connection can keep using its existing broker listener.

Next, choose how to create the certificates:

- **Generate a dedicated Winix CA and certificates** is the easiest option.
- **Issue certificates with my CA and signing key** uses a CA you already own.
- **Import already-issued certificates** uses certificate/key pairs you've
  prepared yourself. See [certificate imports](#certificate-imports) below.

**Retain the CA signing key on HA** is off by default. Leaving it off means HA
uses the key to create the certificates, then doesn't save it. Keeping it lets
you use it to issue certificates later, but this version doesn't renew them
automatically. A retained key lives in HA's configuration folder and may be
included in backups. Saved keys aren't encrypted at rest, so keep those backups
private.

Review the settings and submit. If you already have an installation bundle in
`/config/winix_local/<MAC>/`, use **Use an existing installation bundle** instead.
The form won't overwrite one.

### 4. Configure your broker

Open the Winix Local entry's **Configure** dialog and download:

- **The broker certificate package**, containing `ca.pem`, `broker.pem`,
  `broker.key`, and `requirements.json`.
- **The public provisioning configuration**, `provision.json`, for the setup tool.

Using your broker's settings:

1. Install `broker.pem` and `broker.key` as the TLS listener's server identity.
2. Trust `ca.pem` for client authentication and require a valid client certificate.
3. Map the verified client certificate's common name to the device identity.
4. Apply the publish/subscribe permissions listed in `requirements.json` for the
   purifier and HA's MQTT user.

That file contains the exact topics for your device. Keep the broker's other
users and settings in place. The integration doesn't apply these changes for you.

Keep the ZIP private: it contains the broker's private key. It doesn't contain
the CA signing key or the purifier's private key. The download links expire after
five minutes; reopen Configure for new ones. Anyone with a link can use it until
it expires or its issuing login is revoked, so don't share the links either.

Broker installation itself is outside this guide. For Mosquitto, see the
[configuration reference](https://mosquitto.org/man/mosquitto-conf-5.html).
The [Compose example](examples/mosquitto/compose.yaml) is optional; the HA download
isn't a complete Mosquitto configuration you can mount and run as-is.

### 5. Connect the purifier

Save `provision.json` on your computer, for example in Downloads. Start with the
computer on your **usual network**, where it can reach HA and the broker.

**macOS / Linux**

```sh
.venv/bin/python -m winix_local setup --config "$HOME/Downloads/provision.json"
```

**Windows — PowerShell**

```powershell
.\.venv\Scripts\python.exe -m winix_local setup --config "$HOME\Downloads\provision.json"
```

Change the path if you saved the file somewhere else.

The tool checks the local servers first. When it asks, put the purifier in pairing
mode and switch your computer to its `WINIX SMART ...` network. Then follow the
prompts to choose the purifier's target Wi-Fi and enter its password. You'll see
the settings before confirming that they should be sent.

If the Wi-Fi scan fails, you can enter the network manually. You'll need its name
and the access point's BSSID, which is different from the purifier's MAC.

Once the settings have been sent, reconnect your computer to your usual network.
You don't need USB tethering or a second adapter for this process. Keep `--config`
in the command so the tool uses the certificates you just created in HA.

### 6. Check that it works

Look for fresh readings in HA, then change the fan speed and check that the
purifier responds. A “settings sent” message alone doesn't confirm a connection.

After that, try restarting the purifier without pressing its Wi-Fi button and
check that it reconnects. A long press on Wi-Fi puts it back into pairing mode.
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
| Download link stopped working | Reopen Configure while signed in as an HA administrator |

If the entry is retrying, you can still open Configure to download its files.
Fix the broker/listener problem and reload the entry before running the setup tool.

Back up a working `/config/winix_local/<MAC>/` directory before replacing anything.
If setup stopped after creating that directory, use the existing-bundle option
instead of generating new credentials. The UI doesn't currently edit an existing
installation's hosts or certificates in place. Replacing its CA requires setting
up the purifier again.

### Blocking internet access

This is optional. The purifier talks to HA and your broker locally, but it still
needs DNS and NTP to set its clock for TLS. If you block all internet access,
provide those services locally or redirect them on your network, while allowing
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

Everything below is for working on the project itself.

### Run the checks

From the repository root:

**macOS / Linux**

```sh
.venv/bin/python -m pip install -e '.[dev]'
make validate PYTHON=.venv/bin/python
```

**Windows — PowerShell**

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m black --check src
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m winix_local.build --project-root .
.\.venv\Scripts\python.exe -B -m unittest discover -s src/tests -t src -p 'test_*.py'
```

Create the virtual environment from step 1 first if you haven't already.
`make validate` checks formatting, types, the built ZIP, tests, and the Compose
example when Docker Compose is available. The HA runtime tests need an HA
environment; they're skipped by the ordinary Python suite when HA isn't installed.

The suite has passed on macOS and isolated Linux/HA, including the setup forms,
certificate checks and authenticated downloads. The new UI still needs a complete
real-device walkthrough and restart/reconnection check. Native Windows testing is
also pending. The 5520 and other catalog models haven't been physically verified.

### Repository layout

```text
src/
  winix_local/                  Shared library and setup/build tools
  home_assistant/winix_local/    HA integration
  tests/
    unit/
    integration/
examples/mosquitto/              Optional Compose example
.github/workflows/              CI checks
```

All Python code and tests live under `src/`. The ZIP builder copies shared modules
into the integration's `_core/` package; edit their source in `src/winix_local/`,
not the generated copies. Installing the raw HA source folder won't work without
that build step.

`manifest.json` is the shared HA metadata. `hacs.json` describes the ZIP release
layout. HACS installation will need a published repository and a release asset
named `winix_local.zip`; those haven't been published by this work.

The installed commands are `winix-local`, `winix-local-broker` and
`winix-local-build`. The broker exporter writes Mosquitto-specific files from an
existing private bundle; it is separate from the HA UI's generic certificate ZIP.
