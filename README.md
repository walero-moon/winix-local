# Winix Local

[![Build](https://github.com/walero-moon/winix-local/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/walero-moon/winix-local/actions/workflows/validate.yml)
[Releases](https://github.com/walero-moon/winix-local/releases) · [Issues](https://github.com/walero-moon/winix-local/issues) · [MIT license](LICENSE)

Control your Winix air purifier (and maybe other devices) locally through Home Assistant, using its
stock firmware.

**Status: experimental 0.1.6.** Verified on a stock 5510_US on 2026-11-13
(it's the only hardware I have). The 59-model catalog is just metadata, not a
list of supported devices. Feel free to try it out though.

## Setup

There are some pre-conditions you'll need:

- Home Assistant 2026.10.0 or newer (Container or OS).
- An MQTT broker connected to Home Assistant through its
  [MQTT integration](https://www.home-assistant.io/integrations/mqtt/).
  The broker needs TLS and client-certificate authentication. Tested with Mosquitto,
  but in theory anything that supports these features should work.
- A computer with Wi-Fi, running Windows, macOS, or Linux.
  You only need this during setup, and I don't plan on making a phone app.
- Obvious, but Wi-Fi for the device. Use a 2.4 GHz network for the 5510.

HA and the broker need to be reachable from that Wi-Fi network.
You don't need a Winix account or any hardware modifications.

### 1. Install Winix Local on Home Assistant

Install the Home Assistant integration through [HACS](https://www.hacs.xyz/).
If you don't have HACS yet, follow its
[installation instructions](https://www.hacs.xyz/docs/use/download/download/).

1. Open HACS in Home Assistant, then open the three-dot menu and choose
   **Custom repositories**.
2. Add `https://github.com/walero-moon/winix-local` and select **Integration**
   as the type.
3. Find **Winix Local** in HACS and download it.
4. Restart Home Assistant, then continue with the setup tool below.

<details>
<summary>Manual installation without HACS</summary>

Download `winix_local.zip` from the
[latest release](https://github.com/walero-moon/winix-local/releases/latest).
Extract its contents into Home Assistant's `custom_components/winix_local/`
directory. In HA Container, that's `/config/custom_components/winix_local/`
inside the container. Copy the files into the host folder mapped to that location.

Check that `manifest.json`, `config_flow.py`, and `_core/` are directly inside
`winix_local/`. Back up an existing installation before replacing its files,
then restart Home Assistant. For future updates, download and extract the new ZIP.

</details>

> [!WARNING]
> If you install manually instead of through HACS, you'll need to apply updates yourself.

### 2. Find the purifier's MAC and model

Check the outside labels for a full Wi-Fi MAC address, such as
`AA:BB:CC:DD:EE:FF`, and the exact model identifier, including its regional suffix.
If you already have both, skip to step 3.

A printed model like `5510` may be missing the suffix in `5510_US`. Use the tool
below if either value is missing. The serial number, FCC ID, and short suffix in
`WINIX SMART ...` aren't the full MAC address.

Start on your usual Wi-Fi with internet access. Open Terminal on macOS/Linux or
PowerShell on Windows and run:

**macOS / Linux**

```sh
curl -fsSL https://github.com/walero-moon/winix-local/releases/latest/download/setup.sh | sh -s -- identify
```

**Windows (PowerShell)**

```powershell
& ([scriptblock]::Create((Invoke-RestMethod 'https://github.com/walero-moon/winix-local/releases/latest/download/setup.ps1'))) identify
```

When asked, hold the purifier's Wi-Fi button for about three seconds until
it blinks, or use your model's method for entering pairing mode. Connect to its
network (e.g. `WINIX SMART ...`), then press Enter.

> [!WARNING]
> On an already configured purifier, this can reset its Wi-Fi setup.

You should get the full MAC and model afterwards, for example:

```text
MAC: AABBCCDDEEFF
Model: 5510_US
```

Write down both. You'll need them later. You can reconnect to your regular network
now. If identification fails, see [finding the MAC manually](#finding-the-mac-manually).

> [!TIP]
> Your OS may warn about an unrecognized publisher
> and/or block the download. This is because the executables aren't signed with
> Apple or Microsoft developer certificates, and I don't plan on doing it right now,
> but if the project grows then I'll consider it.

### 3. Add the integration in Home Assistant

Go to **Settings → Devices & services → Add integration → Winix Local**.
Choose **Set up certificates and local endpoints** and enter:

| Field | What to enter |
| --- | --- |
| MAC and model | The values from step 2, including the model suffix, such as `_US` |
| Registration host | HA's LAN address or hostname, reachable from the purifier. If a value is suggested, check it before continuing |
| HTTP / HTTPS ports | Leave these at `18080` and `18443` unless you need different ports |
| MQTT host | The LAN address or hostname of your broker |
| MQTT TLS port | Usually `8883`. It must match your broker's device listener |

Use stable addresses, without `http://`, `https://`, or a port in the hostname.
The purifier needs to reach these addresses itself, so don't use `localhost`.

If HA runs in a container, publish the two registration ports as well. Entering
them in this form won't change Docker's port mappings. If you use VLANs, allow
the purifier and setup computer to reach those ports and the broker's TLS port.
HA's own MQTT connection can keep using its existing broker listener.

Choose **Generate a dedicated Winix CA and certificates** unless you already
manage your own CA or certificates:

- **Generate a dedicated Winix CA and certificates** is the easiest option.
- **Issue certificates with my CA and signing key** uses a CA you already own.
- **Import already-issued certificates** uses certificate/key pairs you've
  prepared yourself. See [certificate imports](#certificate-imports) below.

**Retain the CA signing key on HA** is off by default. HA uses the key to create
the certificates and only saves it if you enable this option. You can use a saved
key to issue certificates later. This version doesn't renew them automatically.

> [!WARNING]
> A retained key lives in HA's configuration folder and may be included in backups.
> Saved keys aren't encrypted at rest, so keep those backups private.

Review the settings and submit. If you already have saved files in
`/config/winix_local/<MAC>/`, choose **Use an existing installation bundle** to
keep them. **Replace an existing installation** creates a private backup first
and asks you to confirm that you'll update the broker and set up the purifier
again. See [replacing an installation](#replacing-an-installation).

### 4. Configure your MQTT broker

Go to **Settings → Devices & services → Winix Local → Configure** and download:

- The broker certificate package, containing `ca.pem`, `broker.pem`,
  `broker.key`, and `requirements.json`.
- The public provisioning configuration, `provision.json`, for the setup tool.

Apply the following in your MQTT broker's configuration. Downloading the files
from HA doesn't apply these settings to the broker:

1. Install `broker.pem` and `broker.key` as the TLS listener's server identity.
2. Trust `ca.pem` for client authentication and require a valid client certificate.
3. Map the verified client certificate's common name to the device identity.
4. Apply the publish/subscribe permissions listed in `requirements.json` for the
   purifier and HA's MQTT user.

Use the exact topics in that file and keep the broker's other users and settings
in place. You'll need to apply these changes yourself.

The ZIP contains the broker's private key, so keep it private. The CA signing key
and the purifier's private key aren't included. The download links expire after
five minutes. Reopen Configure for new ones. Anyone with a link can use it until
it expires or its issuing login is revoked, so don't share the links either.

For broker installation, follow your broker's own instructions. For Mosquitto
settings, see the
[configuration reference](https://mosquitto.org/man/mosquitto-conf-5.html).
The [Compose example](examples/mosquitto/compose.yaml) is optional. The HA download
isn't a complete Mosquitto configuration you can mount and run as-is.

### 5. Connect the purifier

Save `provision.json` on your computer, for example in Downloads. Start on your
**usual network**, with internet access and a route to HA and the broker. Run:

**macOS / Linux**

```sh
curl -fsSL https://github.com/walero-moon/winix-local/releases/latest/download/setup.sh | sh -s -- setup --config "$HOME/Downloads/provision.json"
```

**Windows (PowerShell)**

```powershell
& ([scriptblock]::Create((Invoke-RestMethod 'https://github.com/walero-moon/winix-local/releases/latest/download/setup.ps1'))) setup --config "$HOME\Downloads\provision.json"
```

Change the path if you saved the file somewhere else. The command downloads and
verifies the executable before asking you to switch Wi-Fi. After that, the tool
works without internet access.

The tool checks the local servers first, then asks you to put the purifier in
pairing mode and join its `WINIX SMART ...` network. Follow the prompts to choose
the purifier's target Wi-Fi and enter its password. Review the settings when
prompted, then confirm to send them.

If the Wi-Fi scan fails, you can enter the network manually.

After sending the settings, reconnect to your regular network.
Keep `--config` in the command so the tool uses the installation settings from HA.

### 6. Check that it works (it should!)

Look for fresh readings in HA, then change the fan speed and check that the
purifier responds. A "settings sent" message alone doesn't confirm a connection.

Restart the purifier without pressing its Wi-Fi button and check that it
reconnects. A long press on Wi-Fi puts it back into pairing mode.
Keep HA and the broker running. You can disconnect the setup computer.

## How it works

Winix configures the machine by having you connect to its Wi-Fi network via their app.
The app then sends some configuration parameters to the robot which it then uses to
connect to their cloud system.

This works with the same principle. Except that instead of a mobile app, you just
run the setup once with a Wi-Fi capable computer:

1. Your computer connects to the purifier's setup hotspot and sends your Wi-Fi
   credentials, local server addresses, and CA download URLs.
2. The purifier joins your Wi-Fi and downloads the CA certificate. This lets it
   trust certificates issued for your local services.
3. It contacts the registration service provided by the HA integration, which
   supplies its MQTT credentials and client certificate.
4. The purifier connects to your MQTT broker. The HA integration receives readings
   and sends commands through that broker, giving you local controls in HA.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Winix Local doesn't appear in HA | ZIP extracted into the right folder, HA restarted, and HA version is supported |
| HA entry keeps retrying | HA's MQTT connection, registration port mappings, port conflicts, and HA logs |
| Setup tool can't reach the servers | Computer is on its usual network, listeners are running, and routing/firewall rules allow access |
| TLS or CA fingerprint error | Check `provision.json`, the certificate hostname, and the device clock. Keep certificate verification enabled |
| Can't identify the purifier | Check the hotspot and pairing mode. See [finding the MAC manually](#finding-the-mac-manually) |
| Purifier joins Wi-Fi but stays unavailable | Its routes to HA and MQTT, DNS/NTP, broker client-certificate settings, CN mapping, and topic permissions |
| Setup download fails or the executable won't run | Start on your usual network with internet access. See [setup tool compatibility](#setup-tool-compatibility) |
| Wi-Fi scan fails or the tool asks for a BSSID | See [manual Wi-Fi entry](#manual-wi-fi-entry) |
| Download link stopped working | Reopen Configure while signed in as an HA administrator |

If the entry is retrying, you can still open Configure to download its files.
Fix the broker connection or registration listener, then reload the entry before
running the setup tool.

### Replacing an installation

If you only removed the HA entry or setup stopped partway through, choose
**Use an existing installation bundle** to keep the saved credentials.

To create a replacement:

1. Remove the purifier's existing Winix Local entry from HA, if present. Its saved
   files stay on disk.
2. Add Winix Local and choose **Replace an existing installation**. Enter the
   connection settings, choose certificates, and tick the confirmation box.
3. Open **Configure** to download the new broker package and `provision.json`.
   Apply the new certificates, trusted CA and topic permissions to the broker.
4. Run the setup tool again with the new `provision.json` to reconfigure the
   purifier. Update HA's MQTT connection if its broker address or trust changed.

HA prepares the replacement before moving the old directory into
`/config/winix_local/backups/<MAC>-<timestamp>-<unique ID>/installation/`.
**Configure** shows the backup path. Backups aren't deleted automatically and
contain private keys and credentials. Keep them private, including in HA backups.
If other devices share the broker's TLS listener, changing its CA may require
updating their trust too. Reuse your CA or use a separate listener when needed.

If writing the replacement fails, HA attempts to restore the original directory.
For a manual rollback, remove the HA entry first, then restore the complete old
bundle to `/config/winix_local/<MAC>/` and use the existing-bundle option. If you
already changed the broker or purifier, restore their matching settings too.
Restoring the HA files alone doesn't undo those changes.

### Setup tool compatibility

The standalone downloads cover these platforms:

| Platform | Architecture | OS baseline |
| --- | --- | --- |
| Windows | x64 | Windows 10 or newer |
| macOS | Intel or Apple Silicon | macOS 15 or newer |
| Linux | x64 | glibc-based, Ubuntu 22.04 or equivalent |
| Linux | ARM64 | glibc-based, Ubuntu 24.04 or equivalent |

There are no native builds for Windows ARM64, 32-bit systems, or Alpine/musl.
On macOS/Linux, the download command needs `curl` and either `shasum` or
`sha256sum`. Python isn't required.

### Finding the MAC manually

If automatic identification fails, check for a Wi-Fi MAC on the purifier's labels.
If the purifier is already connected to your Wi-Fi, its entry in your router's
client list is another place to check. Use the purifier's Wi-Fi MAC, not the
router's address.

Append `--mac AABBCCDDEEFF` to the end of the `identify` command in step 2,
replacing the example with your full MAC. The tool will still read the exact
model from the purifier. Start the command on your usual network so it can
download, then join the purifier's hotspot when prompted.

Automatic MAC lookup uses the computer's neighbor table. Linux needs `ip` or
`arp`, and Windows needs `arp.exe`.

### Manual Wi-Fi entry

The tool prompts for the Wi-Fi name (SSID) and access point's BSSID if the scan
fails or you choose manual entry. The BSSID identifies the access point's Wi-Fi
network. It isn't the purifier's MAC.

Look for the BSSID of your target 2.4 GHz network in your router or access point's
Wi-Fi settings. If it isn't listed there, use a Wi-Fi scanner that shows BSSIDs.
With multiple access points, choose the one you want the purifier to connect to.

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
name must be the uppercase MAC without separators. Paste PEM certificate/key pairs.
The form checks that they match.

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
installed. The ordinary Python suite skips them otherwise.

The suite has passed on macOS and isolated Linux/HA, including the setup forms,
certificate checks and authenticated downloads. The new UI still needs a full
setup and restart/reconnection test with a real purifier. The platform test matrix
runs on GitHub Actions. Setting up a real purifier from Windows still needs a
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

The wheel and source archive are written to `dist/`, alongside the HA ZIP. They
remain available for Python users and developers. Ordinary setup uses the native
executables.

To build the executable for the OS and architecture you're on:

```sh
python -m pip install . pyinstaller==6.22.3
python src/tools/build_standalone.py
python src/tools/check_standalone.py --directory dist/native
```

When setup-tool inputs change, the standalone workflow builds each target on its own runner. Its checks exercise
the executable outside the checkout, including certificate generation, catalog
loading, read-only identification against a local TCP fixture, and both successful
and failed checksum checks in the platform launcher. They don't provision hardware
or test installation through HACS.

### Repository layout

```text
src/
  winix_local/                  Shared library and setup/build tools
  home_assistant/winix_local/    HA integration
  tests/
    unit/
    integration/
  tools/                        Executable builds and release checks
  launchers/                    Shell and PowerShell download scripts
examples/mosquitto/              Optional Compose example
.github/workflows/              CI checks
```

All authored code and tests live under `src/`. The ZIP builder copies shared modules
into the integration's `_core/` package. Edit their source in `src/winix_local/`,
not the generated copies. Installing the raw HA source folder won't work without
that build step.

`manifest.json` is the shared HA metadata. `hacs.json` describes the ZIP release
layout. Each tagged release includes `winix_local.zip`, which HACS installs from
this public custom repository. Inclusion in the default HACS catalog is a separate
submission.

The installed commands are `winix-local`, `winix-local-broker` and
`winix-local-build`. The broker exporter writes Mosquitto-specific files from an
existing private bundle. The HA UI provides a separate, generic certificate ZIP.

### Contributing and releases

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and release instructions,
[CHANGELOG.md](CHANGELOG.md) for release notes, and [SECURITY.md](SECURITY.md) for
security reports. The project uses the [MIT license](LICENSE).

Changes to the setup tools run checks on Python 3.9 and 3.13 across Windows,
macOS and Linux, plus the built integration inside HA. HA-only changes run the
Linux Python checks and HA runtime checks. The [Install release workflow](https://github.com/walero-moon/winix-local/actions/workflows/install.yml)
also installs the built wheel in fresh environments on those platforms, using
only prebuilt dependencies. HA-only releases check the new wheel on Linux and
reuse the previous release's setup executables after checking their source/build
inputs and published checksums. Tool changes require the full platform and native
executable checks. Launchers and checksums are prepared for each new release.

## Acknowledgments

The device catalog is based on the model and sensor definitions in
[iprak/winix](https://github.com/iprak/winix). Thanks to its contributors for
mapping those devices.
