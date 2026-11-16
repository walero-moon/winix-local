"""Console entry point for the bundled provisioning tool."""

import sys

from winix_local.cli import main

if __name__ == "__main__":
    if sys.argv[1:] == ["--self-check"]:
        from winix_local.model import load_builtin_catalog

        catalog = load_builtin_catalog()
        if "5510_US" not in catalog.model_ids or "5520_US" not in catalog.model_ids:
            raise SystemExit("Bundled model catalog is incomplete")
        print("Bundled model catalog loaded.")
    else:
        main()
