# Changelog

## [0.1.1] - 2026-11-16

- Standalone setup executables for Windows x64, macOS Intel/Apple Silicon, and
  Linux x64/ARM64. Python no longer needs to be installed on the setup computer.
- Shell and PowerShell commands download the correct executable, verify its
  release checksum, and run identification or guided provisioning.
- A read-only `identify` command replaces the Python snippet for finding the
  purifier MAC and model.
- Native executable and launcher checks gate releases alongside the existing
  wheel, protocol and HA checks. Each binary ships with bundled software notices.
- HACS is the main HA installation method in the README. The repository is public.

The binaries are unsigned by Apple/Microsoft. Device support and the pending
real-device HA setup/reconnection checks are unchanged from 0.1.0.

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

The repository was private when 0.1.0 was first released.
