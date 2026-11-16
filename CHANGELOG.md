# Changelog

## [0.1.0] - 2026-11-16

First experimental release.

- Local registration and MQTT control for stock Winix firmware, without a Winix
  account or hardware modification.
- HA setup forms for generating certificates, importing a CA/signing key, or
  importing issued certificates. CA signing-key retention defaults to off.
- Broker-neutral certificate downloads and per-device topic requirements.
- Guided Wi-Fi provisioning for Windows, macOS and Linux, including MAC discovery.
- A HACS-format integration ZIP, Python wheel, source archive and SHA-256 checksums.

Local registration, certificate trust, MQTT telemetry and low/medium fan control
were physically verified on a 5510_US. The complete new HA UI setup and normal
restart/reconnection still need a real-device walkthrough. Other model entries
are catalog metadata, not a supported-device list.

The repository and release are private for now. Download the ZIP with an account
that has access and install it manually; this isn't a public HACS listing.
