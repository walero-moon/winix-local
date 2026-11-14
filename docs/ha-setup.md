# Home Assistant certificate setup

The built HACS ZIP contains a guided setup UI. This change has not been deployed.
Only the 5510 has been hardware verified; other models use protocol fixtures.

## Prerequisites

Install the built integration and configure HA's MQTT integration to use your
chosen broker. Winix Local uses that MQTT client and does not store another broker
login. The purifier-facing endpoint can use a different listener on the same broker.

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

Obtain the normalized MAC and exact model from a verified installation or the
provisioning tool's read-only handshake. Do not infer the exact model identifier
from the catalog alone.

## Add the device

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

## Download and finish

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

After the broker and registration listeners are reachable, run
`winix-local setup --config provision.json` on a Windows, macOS, or Linux computer
connected to the purifier setup Wi-Fi. Follow the prompts for the target Wi-Fi.
Rejoin the normal network and confirm fresh telemetry in HA; sending setup frames
alone does not prove success.

An internet-blocked purifier additionally needs reachable DNS/NTP so its clock is
valid for TLS. This UI does not configure firewall, DHCP, NTP, automatic certificate
renewal, or install an MQTT broker.
