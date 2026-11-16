# Installation and Home Assistant setup

This guide uses the HA setup UI to prepare certificates, an existing MQTT broker
for messaging, and a separate computer for one-time Wi-Fi provisioning. No Winix
account or hardware modification is required by this local setup path.

**Experimental 0.1.0.** Local registration, certificate trust, MQTT and low/medium
fan control were verified on a stock 5510_US on 2026-11-13. The complete new HA UI
workflow and reboot/reconnection acceptance checks remain pending on real hardware.
Other models, including the 5520, have protocol fixtures rather than physical
verification. Native Windows execution also remains unverified.

There is no published release assumed by this guide. Build the HACS-format ZIP
locally and install it manually. HACS metadata is prepared for a future ZIP
release; a raw checkout is not a HACS installation artifact.

[Back to the project README](../README.md).

## Prerequisites

You need:

- Home Assistant 2026.10.0 or newer, with access to its configuration directory.
  HA Container is supported; HA OS and an app/add-on are not required.
- An existing compatible MQTT broker and HA's [MQTT integration](https://www.home-assistant.io/integrations/mqtt/)
  configured to connect to it. Broker installation is outside this guide.
- A Windows, macOS, or Linux computer with Python 3.9+ and Wi-Fi. Install the
  Python tools while it still has internet access; provisioning makes no cloud
  requests and does not require a second internet connection.
- The purifier and a compatible target Wi-Fi network. Use 2.4 GHz for the 5510.

Winix Local uses HA's existing MQTT client and does not store another broker
login. The purifier-facing endpoint can use a different listener on the same broker.
Keep the broker and HA running after setup; the provisioning computer is temporary.

For multiple purifiers, use distinct registration HTTP/HTTPS ports for each HA
entry. Purifiers sharing one broker TLS listener must trust its server certificate:
reuse a common imported CA, or configure separate broker TLS listeners for separate
CAs. Generating a new CA for a second device does not update existing devices.

The broker must support TLS 1.2+, client-certificate authentication, identity mapping
from the verified certificate, and per-identity topic permissions. The setup UI is
broker-neutral. Mosquitto is an optional example elsewhere in the repository.

The purifier must reach both the broker's TLS listener and HA's local registration
HTTP/HTTPS listeners. HA Container needs the selected ports published explicitly;
entering ports in a form does not change Docker or firewall settings.

### Network access

Choose stable LAN addresses or DNS names. The certificate names must match the
addresses you enter; avoid temporary USB-tethering addresses and `localhost`.
The registration host is HA's reachable address, not necessarily the broker host.
Publish ports to the HA container when necessary; a reverse proxy serving only
HA's normal web UI does not expose these separate registration listeners.

| Source | Destination | Default TCP ports | Purpose |
| --- | --- | --- | --- |
| Setup computer on normal LAN | HA registration server | 18080, 18443 | CA download and pinned TLS preflight |
| Setup computer on normal LAN | MQTT broker | 8883 | TCP reachability preflight |
| Purifier on target Wi-Fi | HA registration server | 18080, 18443 | CA bootstrap and HTTPS registration |
| Purifier on target Wi-Fi | MQTT broker | 8883 | TLS with client-certificate authentication |
| HA | Same MQTT broker | Your existing MQTT listener | Telemetry subscriptions and control messages |

Allow the required routes between the actual devices, including across VLANs.
There is no requirement to expose these services to the public internet. If you
change default ports, use the same values in HA, container mappings and broker
configuration. Plan DNS/NTP separately if blocking the purifier's internet access;
see the offline note below.

## 1. Install the Python tools and build the HA ZIP

From the repository root on the setup computer, use one set of commands.

macOS/Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
.venv/bin/python -m winix_local.build --project-root .
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m winix_local.build --project-root .
```

No OpenSSL executable is needed. The build creates `dist/winix_local.zip` and does
not change any purifier or server settings. Python tools are needed on the setup
computer only; do not pip-install this project into HA's managed Python environment.

Copy the ZIP to the HA server and extract its **contents** into
`<HA configuration directory>/custom_components/winix_local/`. For HA Container,
that directory is `/config/custom_components/winix_local/` inside the container;
use its mapped configuration directory on the host. Back up an existing integration
directory before replacing its files.

The destination should contain `manifest.json`, `config_flow.py`, and `_core/`
directly inside `winix_local/`, with no extra nested ZIP or project directory. Do
not copy `src/home_assistant/winix_local/` alone: it lacks the build-generated core.
Restart HA after installing the custom integration, then open **Settings > Devices
& services > Add integration**. The integration is named **Winix Local**.

## 2. Identify the purifier without changing its settings

Skip this step if you already have a verified full MAC and exact model identifier.
Otherwise put the purifier in pairing mode (on the 5510, hold Wi-Fi for about three
seconds until it blinks), then join its `WINIX SMART ...` network on the setup
computer. Entering pairing mode may replace an existing Wi-Fi setup. The Python
command below sends only an identity handshake, not Wi-Fi or server settings.

macOS/Linux, from the same repository root:

```sh
.venv/bin/python -c "from winix_local.discovery import identify_device; print(identify_device())"
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -c "from winix_local.discovery import identify_device; print(identify_device())"
```

The result includes the normalized MAC and exact model, for example
`('AABBCCDDEEFF', '5520_US')`. This is illustrative, not proof of 5520 compatibility.
Automatic MAC discovery supports macOS, Linux and Windows neighbor tables. Linux
needs `ip` or `arp`; Windows uses `arp.exe`. The device handshake verifies the
candidate identity. If lookup fails and you know the full purifier MAC, call
`identify_device(mac='AABBCCDDEEFF')` instead, replacing the example MAC. The short
suffix in the Wi-Fi name and the FCC ID are not a substitute for the full MAC.
Do not infer the model from the catalog alone.

Reconnect the computer to its normal LAN afterward. Leave the purifier in pairing
mode, or re-enter it when the provisioning wizard asks.

## 3. Add the device in HA

Add **Winix Local**, choose **Set up certificates and local endpoints**, and enter:

- Device MAC and exact model, such as `5520_US`.
- Registration server LAN hostname/IPv4 address and HTTP/HTTPS ports.
- Broker LAN hostname/IPv4 address and purifier-facing TLS port.

Choose one certificate source:

1. **Generate a dedicated Winix CA and certificates.** Creates a CA and three
   certificate/key pairs: registration server, broker server, and purifier client.
2. **Issue certificates with my CA and signing key.** Paste one PEM CA certificate
   and its matching unencrypted RSA signing key; HA issues the three leaf pairs.
3. **Import already-issued certificates.** Paste the CA and all three PEM
   certificate/key pairs. Supplying a CA signing key is optional.

Imports support one RSA CA directly signing RSA certificates, with keys of at least
2048 bits. Intermediate chains, EC keys, wildcard-only hostname SANs, encrypted PEM
keys, and separate registration/broker CAs are not supported by this flow. This is
our conservative import profile, not a claim about every firmware's TLS support.
Certificates must be currently valid. Server certificates need server-auth EKU and
exact configured DNS/IP SANs. The purifier certificate needs client-auth EKU and a
CN equal to the uppercase MAC without separators. Invalid material is rejected
before writing installation files. Private PEM fields are not prefilled on errors.

**Retain the CA signing key on HA** defaults to off. With retention off, the signing
key is only used in memory and is not written to disk. Keep your original imported
key backup to issue replacement certificates later. A discarded generated CA key
cannot be recovered; replacing that CA requires reprovisioning the purifier.
Retention does not enable automatic renewal in this release.

A retained key is stored as a permission-restricted PEM file beneath
`/config/winix_local/<MAC>/`. It is not encrypted at rest and may be included in HA
backups. Anyone who obtains it can issue identities trusted by that CA. Prefer a
dedicated Winix CA over a general home/organization root. Use a trusted HTTPS
connection to HA when supplying private keys, and protect HA and its backups.

Review and submit. Existing installations are never overwritten. The config entry
contains only the normalized MAC; private material stays in the bundle directory.
Certificate work runs in an executor. HA starts the registration listeners when
loading the entry. No broker, purifier, Wi-Fi or cloud configuration is changed by
this setup flow. Existing bundles still have a separate import path.

## 4. Download the files and configure the broker

Open the entry's **Configure** dialog as an HA administrator. It provides:

- A **broker certificate package** containing `ca.pem`, `broker.pem`, `broker.key`,
  and broker-neutral `requirements.json` with exact per-device topic grants.
- A **public provision.json** containing model, MAC, endpoints and CA fingerprint.
  It contains no token, private key, or Wi-Fi password.

Download links are signed for five minutes and bound to the current administrator
login. Revoking that login invalidates them. Reopen Configure for fresh links.
Signed URLs are temporary bearer credentials: anyone holding one can use it until
it expires or its login is revoked. Do not share them. Downloads require
administrator authorization and are not cacheable. The bootstrap
HTTP listener does not serve these export routes.

The broker ZIP contains its private server key: keep the download private. It never
includes the CA signing key or purifier client key. Use your broker's own settings
to install the server identity, trust the supplied CA for client authentication,
map the verified client CN to the purifier identity, and apply the exact topic
grants. Give HA's MQTT identity the complementary grants. Keep unrelated broker
configuration intact. Nothing is applied to the broker automatically.

Use `requirements.json` as the source of exact topic names. A general MQTT login
alone does not configure the purifier's certificate authentication. MQTT discovery
does not replace this step: Winix Local creates its own HA entities.

The [Mosquitto Compose example](../examples/mosquitto/compose.yaml) is optional.
The HA broker ZIP is broker-neutral and does **not** contain `mosquitto.conf`, a
password file or Mosquitto ACL files, so mounting that ZIP's contents alone will
not make the example operational. Configure your broker using its own documented
settings; for Mosquitto, see its [configuration reference](https://mosquitto.org/man/mosquitto-conf-5.html).
The standalone `winix-local-broker` exporter is a separate, Mosquitto-specific
workflow for an existing private bundle. Do not run it against an existing broker
and replace that broker's configuration blindly.

Keep HA's MQTT connection working while adding the device TLS listener. If the HA
entry is retrying because the broker or registration ports were unavailable, fix
them and reload that entry through HA. Confirm the registration listeners are
running before provisioning. The Configure dialog can provide exports even while
entry setup is failing; downloads do not prove the listeners are ready.

## 5. Provision the purifier

Download `provision.json` onto the setup computer. Keep it outside the Git checkout;
it is public configuration, but need not be committed. Start with the computer on
its **normal LAN**, where it can reach HA and the broker. Substitute the downloaded
file's actual path; quoting paths with spaces is supported.

macOS/Linux:

```sh
.venv/bin/python -m winix_local setup --config "$HOME/Downloads/provision.json"
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m winix_local setup --config "$HOME\Downloads\provision.json"
```

The wizard checks the downloaded CA against the file's fingerprint, verifies
registration HTTPS, and checks broker TCP reachability. This is not yet a test of
purifier MQTT authentication. **Only after those checks** does it prompt you to
join the purifier's `WINIX SMART ...` hotspot. Do not switch networks early; no USB
tethering or second adapter is required for this sequence.

Once connected to the hotspot, press Enter. The wizard verifies the MAC and model,
asks you to select the target Wi-Fi, prompts privately for its password, and shows
a final confirmation before sending server and Wi-Fi settings. It never switches
your computer's Wi-Fi automatically. If the Wi-Fi scan is unavailable, manual entry
requires the SSID and the target access point's **BSSID**, not the purifier MAC.
Use the intended IoT network, not a temporary lab network unless you want one.

After sending, return the computer to the normal LAN. The purifier should join the
target Wi-Fi and contact HA's registration service and your MQTT broker. A closed
setup socket or a “messages sent” result is not proof of success. Do not run the
wizard without `--config` for this HA-created installation: that is the separate
standalone bundle-generation path and would create different credentials.

## 6. Verify and recover

Confirm fresh telemetry and availability in HA, then make a small fan-speed change
and observe the purifier responding. Reconnects and a power cycle without pressing
the Wi-Fi button are useful acceptance tests after initial operation. A long Wi-Fi
button press can re-enter pairing mode rather than test a normal restart. The new
UI-to-purifier walkthrough and persistence checks remain owner acceptance tests,
not checks this guide claims have already passed.

| Symptom | Check |
| --- | --- |
| Winix Local is absent from Add integration | Correct extracted ZIP layout, HA version, and HA restart after installation |
| Existing installation is refused | Use **Use an existing installation bundle** for `/config/winix_local/<MAC>/`; never delete a working bundle just to retry the form |
| HA entry retries or is not ready | HA's MQTT connection, registration port conflicts/mappings, filesystem access, and HA logs |
| Preflight cannot reach local services | Setup computer is on normal LAN; routes, DNS, ports and listeners are reachable |
| CA fingerprint or hostname verification fails | Use the matching provision.json; inspect host/SAN and the installed certificates instead of disabling TLS verification |
| Hotspot identification fails | Pairing mode, connection to the correct hotspot, OS neighbor command availability, or explicit full MAC fallback |
| Purifier joins Wi-Fi but stays unavailable | Purifier's routes and clock, broker TLS/client-certificate configuration, CN mapping and the exact topic grants |
| Download link fails | Reopen Configure as an administrator for fresh links; do not share or modify signed URLs |

Commands are deliberately non-optimistic: availability requires fresh, non-retained
telemetry. Commands are non-retained, and the integration refuses commands while
unavailable. Model metadata does not guarantee every control is supported.

Back up a working private bundle before intentional replacement. Re-importing an
existing bundle uses its original CA and token. The current UI does not provide an
in-place model/host/certificate editing flow or automatic renewal. Changing hosts
requires matching certificates and updated provisioning settings; switching CAs
requires reprovisioning. Do not hand-edit a working installation as a troubleshooting
shortcut. If setup was interrupted after creating a complete bundle, use the
existing-bundle path rather than generating a second identity.

### Optional: block the purifier's internet access

Local control does not require the purifier to reach Winix's cloud. Blocking all
internet access is optional, and firewall changes are outside this setup UI. A
fully blocked device still needs working DNS and NTP for a correct TLS clock.
Permit those services locally or provide scoped redirection appropriate to your
network. Keep the registration and broker routes allowed. Do not disable certificate
verification to work around clock errors.

The UI does not configure firewall, DHCP or NTP. The repository's optional per-device
DNS/NTP helper is an advanced separate configuration; it is not enabled by the new
HA form. It uses privileged UDP 53/123, which may conflict with existing DNS services
or require additional permissions. Do not enable it on a shared server casually.

## What has been validated

The source has automated certificate, wire-protocol and provisioning tests. The
built ZIP has isolated HA tests for setup forms, form serialization and authenticated
downloads, including expiration and login revocation. The test suite has passed on
macOS and in isolated Linux/HA. Windows CI is configured but native Windows execution
is pending. Package checks verify a clean wheel install and independent HACS ZIP
reproduction from the source archive. These checks do not replace the physical
acceptance steps above.

The manifest's GitHub documentation and issue URLs are publication targets. They
will only describe this checkout after the repository and the relevant branch are
published; this documentation pass does not publish them.
