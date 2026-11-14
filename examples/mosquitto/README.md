# Optional Mosquitto example

Use an existing compatible broker or this generic Compose example. Generate a
private installation bundle and broker export first, as described in
[installation](../../docs/installation.md). Keep private files outside Git.

Create a separate private `mosquitto-config/` directory. Copy only `ca.pem`,
`broker.pem`, `broker.key`, `passwords`, `device.acl`, `backend.acl` and
`mosquitto.conf` from the generated `mosquitto-export/` into it. Keep the export's
`ha-credentials.json` separately; do not mount the whole export directory.

Copy `.env.example` to a private `.env`, set the broker server's LAN bind address
and the absolute path to the private Mosquitto configuration directory. The
container runs as UID/GID 1883, so make the configuration readable by that user.
Keep `ha-credentials.json` outside the configuration mount and never copy the
CA signing key into it.

Validate without starting anything:

```sh
docker compose --env-file .env config --quiet
```

When you choose to deploy it, the configuration exposes a password-authenticated
backend listener on 1883 and a device mTLS listener on 8883. Backend 1883 is
unencrypted and requires a trusted LAN. Do not expose either listener to WAN.
The example contains no host-specific VLAN/macvlan rules, network addresses or
Ansible deployment logic. Existing broker users should merge the necessary
listeners/ACLs rather than replace their current configuration.
