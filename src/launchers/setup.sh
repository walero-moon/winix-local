#!/bin/sh
# Download a release executable, check its hash, then pass arguments unchanged.
set -eu
release_tag='__RELEASE_TAG__'
base="https://github.com/walero-moon/winix-local/releases/download/$release_tag"

case "$(uname -s)/$(uname -m)" in
    Darwin/arm64) asset=winix-local-macos-arm64 ;;
    Darwin/x86_64) asset=winix-local-macos-x86_64 ;;
    Linux/x86_64) asset=winix-local-linux-x86_64 ;;
    Linux/aarch64|Linux/arm64) asset=winix-local-linux-arm64 ;;
    *) echo 'No executable for this OS/architecture. See the README.' >&2; exit 1 ;;
esac
command -v curl >/dev/null || { echo 'Install curl first.' >&2; exit 1; }
if command -v sha256sum >/dev/null; then
    hash_tool=sha256sum
elif command -v shasum >/dev/null; then
    hash_tool=shasum
else
    echo 'Install sha256sum or shasum first.' >&2
    exit 1
fi

umask 077
work=$(mktemp -d "${TMPDIR:-/tmp}/winix-local.XXXXXXXX")
trap 'rm -rf "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2     "$base/$asset" --output "$work/$asset"
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2     "$base/SHA256SUMS" --output "$work/SHA256SUMS"
expected=$(awk -v name="$asset" '$2 == name { print $1 }' "$work/SHA256SUMS")
if [ "$hash_tool" = sha256sum ]; then
    actual=$(sha256sum "$work/$asset" | awk '{print $1}')
else
    actual=$(shasum -a 256 "$work/$asset" | awk '{print $1}')
fi
if [ "${#expected}" -ne 64 ] || [ "$actual" != "$expected" ]; then
    echo 'Checksum verification failed. Nothing was run.' >&2
    exit 1
fi

chmod 700 "$work/$asset"
if [ "$#" -eq 0 ]; then set -- setup; fi
# A curl | sh invocation consumes stdin; take interactive input from the terminal.
if [ ! -t 0 ] && [ -t 1 ]; then
    "$work/$asset" "$@" </dev/tty
else
    "$work/$asset" "$@"
fi
