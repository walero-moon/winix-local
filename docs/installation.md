# Install Winix Local

Status: experimental 0.1.0. Stock 5510_US only. The local protocol and fan low /
medium control were verified on 2026-11-13. Shared constants and model profiles
are separated into `catalog.json` data, a transport-independent `model.py`
validator, and typed control choices in `const.py`.
The catalog includes multiple product families; local registration/control is
currently validated and restricted to the 5510_US. See the project README for support and packaging status.

## Components

1. Mosquitto or a compatible reachable MQTT broker.
2. The `winix_local` custom integration, installed in HA's `custom_components`.
   It owns native fan/sensor/PlasmaWave entities and local HTTP/HTTPS registration.
3. A one-time laptop provisioning helper. No account email/password, Winix cloud
   enrollment or firmware update is performed by these tools.

The HACS build creates a self-contained integration ZIP. This version is
not published in HACS's default catalog; until a public release exists, install the built ZIP manually. A future HACS
release must provide the ZIP asset named in root hacs.json.
The built-in MQTT-endpoint mode discussed during design is not implemented in
0.1.0: this version requires an external broker and HA's MQTT integration.

## Network prerequisites

Choose stable LAN addresses for HA and the broker. The purifier must reach:

| Destination | Default ports | Purpose |
|---|---|---|
| HA integration | TCP 18080 | Download public CA |
| HA integration | TCP 18443 | Local TLS registration |
| Broker | TCP 8883 | Device MQTT over TLS |
| Working DNS / NTP | UDP 53 / 123 | Resolve time server and obtain correct time |

HA must reach the broker's backend listener (1883 by default). The provided
backend listener uses passwords on a trusted LAN; it is not encrypted. Do not
expose it to the internet or an untrusted network. For existing brokers, configure
HA MQTT with the appropriate TLS/authentication settings instead. Device TLS and
backend-client authentication are independent.

HA Container users on bridge networking must publish the extra 18080/18443 ports,
or use a suitable host/macvlan configuration. Docker service names cannot be
used as the purifier's broker address unless its DNS can resolve them.

## Prepare installation once

The current helper supports macOS/Linux and requires Python 3.9+ and
`/usr/bin/openssl`. From this repository, create a virtual environment and install
the package with `pip install -e .`. Use a private directory outside Git:

```sh
python3 -m venv .venv-winix
.venv-winix/bin/python -m pip install --upgrade pip
.venv-winix/bin/python -m pip install -e .
.venv-winix/bin/winix-local init \
  --mac AA:BB:CC:DD:EE:FF \
  --server-host 192.168.1.20 \
  --mqtt-host 192.168.1.21 \
  --out /private/path/winix-installation
.venv-winix/bin/winix-local-broker \
  --bundle /private/path/winix-installation \
  --out /private/path/mosquitto-export
```

`init` refuses a nonempty directory to prevent accidentally replacing an installed
CA. Back up the private bundle securely. The files include CA/server/device keys
and a stable ten-character registration token. `provision.json` alone contains
public setup addresses, MAC and CA fingerprint, without private keys or token.
A fresh export currently creates a new backend password; reuse an existing export
unless deliberately rotating HA's broker credentials.

Copy the **whole installation bundle** to HA's persistent config directory:
`/config/winix_local/AABBCCDDEEFF/`. Files should be owner-readable only.

Build the HACS archive with `.venv-winix/bin/winix-local-build --project-root .`.
Extract `dist/winix_local.zip` into `/config/custom_components/winix_local/`.
The archive contains the directory's contents directly. Do not copy the raw HA
source directory: the build supplies its shared `_core/` implementation.
The CA key is not used by the runtime listeners; retain it for future controlled
renewal. The generated certificates expire in December 2037. Automatic certificate
rotation is not implemented or validated yet. Do not regenerate the CA on upgrades.

Install only these broker export files into the broker config directory:
`ca.pem`, `broker.pem`, `broker.key`, `passwords`, `device.acl`, `backend.acl`,
`mosquitto.conf`. Make the directory readable by broker UID 1883 and keep keys and
password files mode 0600. Do not put the CA signing key in the broker container.
Keep `ha-credentials.json` separately; use it when configuring HA's MQTT integration.

The exported Mosquitto configuration has:

- A password-authenticated backend listener on 1883, with HA control/event ACLs.
- A device listener on 8883 requiring the device's locally issued client
  certificate. Its certificate CN becomes the ACL username; it can read only
  its own Control topic and write only its own Conn/Event topics.

For an existing broker, merge equivalent listeners and ACLs without replacing
other devices' credentials or permissions. The export helper currently targets
one purifier, not a multi-device certificate registry. Valetudo requires its own
user and topic permissions; it is not preconfigured by this export.

## Start broker and integration

For generic Docker Compose, copy `examples/mosquitto/.env.example` to a private env
file, set the LAN bind address and injected config directory, then validate and
start the `mqtt` project.

Restart HA once after copying the integration. In **Settings → Devices & services**:

1. Add **MQTT**, enter the broker's backend endpoint and the generated HA credentials.
2. Add **Winix Local**, enter the purifier's MAC. The integration reads its prepared
   bundle and starts the registration listeners. The MAC identifies entities,
   not the replaceable token. No account password is requested.

Listener-port conflicts appear as integration setup failures. Unloading/reloading
the integration closes/restarts its listeners and removes MQTT subscriptions.
Only one installation bundle / purifier is currently supported per HA listener
port pair; multi-device shared listeners are future work.

## Provision the purifier

Keep the local services running. Hold Wi-Fi about 3 seconds until blinking, then
join `WINIX SMART ...` on the setup laptop. Copy `provision.json` to the laptop.
Find the **target** network's 2.4 GHz AP BSSID from your router (not the purifier
hotspot's MAC). The helper does not switch your laptop's networks automatically.

```sh
.venv-winix/bin/winix-local provision \
  --config /private/path/winix-installation/provision.json \
  --ssid Your-IoT-WiFi --bssid AA:BB:CC:11:22:33
```

It prompts for the Wi-Fi password without logging it, verifies the 5510 identity,
sends DP0200 server/CA settings, then DP0400 Wi-Fi settings. A closed setup socket
can be a normal Wi-Fi transition, so it is **not** taken as proof of success.
Rejoin the normal network and wait for live HA telemetry. Commands are not retained
or queued while the device is unavailable. State changes follow device feedback.

`--check-only` identifies the device without writing settings (SSID/BSSID options
are still accepted/required by this first CLI version). Only `5510_US` is allowed;
compatibility with other Winix models is not established by the upstream model list.

## Strictly offline operation (optional)

Normally an internet-reachable NTP service should supply time. Internet NTP with
our local backend remains an explicit acceptance test, not a demonstrated result.
If you block WAN, provide local DNS/NTP; we observed a `pool.ntp.org` query and
success after serving local time. For the optional built-in device-only DNS/NTP sink, add
`--offline-device-ip <reserved purifier IP>` when running `init`. HA then needs
permission to bind UDP 53/123, and only that device IP receives responses.
Point only its isolated network DNS to the HA address. The normal installation
does not enable these listeners.
Do not alter your whole home's DNS to provision one purifier. Retain narrow
firewall exceptions for registration, MQTT and the chosen local time service.

## Acceptance and rollback

Before moving a working device, preserve its old bundle, server configuration and
network settings. Verify broker login, device TLS, topic ACLs and HA listener
reachability first. Then test low → medium fan control and confirm device-reported
state; separately test power, auto/manual, PlasmaWave, cold boot and server restart.

Rollback boundary: before re-provisioning, removing the new integration / stopping
the new broker does not change the purifier. After re-provisioning, put it back in
pairing mode and reapply the preserved configuration using the old backend. Keep
its existing WAN block until the owner deliberately chooses otherwise. Remove
only the `mqtt` Compose project, retaining its volume and private PKI for recovery.
No HA configuration database edits or firmware operations are required.
