"""Installed provisioning command with the same concise CLI error reporting."""

import sys

from .provision import main as provision_main


def main() -> None:
    try:
        provision_main()
    except (OSError, ValueError, KeyError) as error:
        print("Setup failed: " + str(error), file=sys.stderr)
        raise SystemExit(1) from error
