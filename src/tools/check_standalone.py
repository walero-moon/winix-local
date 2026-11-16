"""Exercise a frozen executable and its verified-download launcher without hardware."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def check_standalone(directory: Path) -> None:
    executables = [
        path
        for path in directory.glob("winix-local-*")
        if not path.name.endswith("-licenses.txt")
    ]
    if len(executables) != 1:
        raise ValueError("Expected one executable for the current runner")
    executable = executables[0].resolve()
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)

    # Import only the TCP test peer in this controller process. The child binary
    # runs from an unrelated directory and cannot import anything from the checkout.
    sys.path.insert(0, str(ROOT / "src"))
    from tests.integration.test_provisioning import PurifierFixture

    with tempfile.TemporaryDirectory(prefix="winix-native-check-") as temporary:
        work = Path(temporary)

        def run(arguments, **kwargs):
            return subprocess.run(
                [str(executable), *arguments],
                cwd=work,
                env=environment,
                text=True,
                capture_output=True,
                timeout=90,
                **kwargs,
            )

        for arguments in (["--help"], ["setup", "--help"], ["--self-check"]):
            result = run(arguments)
            if result.returncode:
                raise ValueError(result.stdout + result.stderr)

        output = work / "certificates with spaces"
        result = run(
            [
                "init",
                "--mac",
                "AABBCCDDEEFF",
                "--model",
                "5520_US",
                "--server-host",
                "127.0.0.1",
                "--mqtt-host",
                "127.0.0.1",
                "--out",
                str(output),
            ]
        )
        if result.returncode:
            raise ValueError(result.stdout + result.stderr)
        config = json.loads((output / "provision.json").read_text())
        if config["model"] != "5520_US" or not config["ca_sha256"]:
            raise ValueError("Bundled certificate generation failed")

        for model in ("5510_US", "5520_US", "FUTURE_MODEL_US"):
            with PurifierFixture(model) as peer:
                result = run(
                    [
                        "identify",
                        "--no-prompt",
                        "--mac",
                        "AABBCCDDEEFF",
                        "--setup-host",
                        "127.0.0.1",
                        "--setup-port",
                        str(peer.port),
                    ]
                )
                if result.returncode or model not in result.stdout or peer.commands:
                    raise ValueError("Read-only identity test failed: " + result.stderr)

        # Reach the lazy guided-setup imports and preserve a path containing spaces.
        result = run(["setup", "--config", str(work / "missing config.json")])
        if result.returncode == 0 or "Setup failed:" not in result.stderr:
            raise ValueError("Expected a clean missing-config error")

        check_launcher(executable, work, environment)
    print("Executable, crypto, catalog, TCP identity and launcher checks passed.")


def check_launcher(executable: Path, work: Path, environment: dict) -> None:
    """Use local release fixtures; verify hashes before allowing any execution."""
    downloads = work / "downloads"
    downloads.mkdir()
    shutil.copy2(executable, downloads / executable.name)
    checksum = downloads / "SHA256SUMS"
    checksum.write_text(
        f"{hashlib.sha256(executable.read_bytes()).hexdigest()}  {executable.name}\n"
    )
    windows = os.name == "nt"
    name = "setup.ps1" if windows else "setup.sh"
    launcher = work / name
    launcher.write_text(
        (ROOT / "src/launchers" / name).read_text().replace("__RELEASE_TAG__", "v0.1.1")
    )
    environment["FIXTURE_DOWNLOADS"] = str(downloads)
    if windows:
        wrapper = work / "check.ps1"
        wrapper.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            "function Invoke-WebRequest { param($UseBasicParsing, $Uri, $OutFile) "
            "Copy-Item -LiteralPath (Join-Path $env:FIXTURE_DOWNLOADS "
            "([Uri]$Uri).Segments[-1]) -Destination $OutFile }\n"
            "& $env:FIXTURE_LAUNCHER @args\n"
        )
        # Match the switch accepted by the real PowerShell downloader.
        wrapper.write_text(
            wrapper.read_text().replace(
                "param($UseBasicParsing", "param([switch]$UseBasicParsing"
            )
        )
        environment["FIXTURE_LAUNCHER"] = str(launcher)
        command = ["pwsh", "-NoProfile", "-File", str(wrapper)]
    else:
        stub = work / "bin"
        stub.mkdir()
        curl = stub / "curl"
        curl.write_text(
            '#!/bin/sh\nset -eu\nwhile [ "$#" -gt 0 ]; do\n'
            ' case "$1" in https:*) url=$1 ;; --output) shift; output=$1 ;; esac\n'
            ' shift\ndone\ncp "$FIXTURE_DOWNLOADS/${url##*/}" "$output"\n'
        )
        curl.chmod(0o700)
        environment["PATH"] = str(stub) + os.pathsep + environment["PATH"]
        command = ["sh", str(launcher)]

    def launch(arguments):
        return subprocess.run(
            [*command, *arguments],
            cwd=work,
            env=environment,
            text=True,
            capture_output=True,
            timeout=90,
        )

    result = launch(["setup", "--help"])
    if result.returncode or "--config" not in result.stdout:
        raise ValueError("Launcher failed: " + result.stdout + result.stderr)
    result = launch(["setup", "--config", str(work / "missing config.json")])
    if result.returncode == 0 or "Setup failed:" not in result.stderr:
        raise ValueError("Launcher did not preserve arguments or failure status")

    checksum.write_text(f"{'0' * 64}  {executable.name}\n")
    result = launch(["--help"])
    if result.returncode == 0 or "Checksum verification failed" not in result.stderr:
        raise ValueError("Launcher ran an executable with a bad checksum")
    if "usage:" in result.stdout:
        raise ValueError("Bad-checksum executable was run")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    check_standalone(args.directory)


if __name__ == "__main__":
    main()
