"""Build a native executable on the target operating system."""

import argparse
from importlib import metadata
import os
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig


def asset_name() -> str:
    operating_system = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}
    architecture = platform.machine().lower()
    architecture = {"amd64": "x86_64", "aarch64": "arm64"}.get(
        architecture, architecture
    )
    system = operating_system.get(platform.system())
    if system is None or architecture not in ("x86_64", "arm64"):
        raise ValueError("No standalone build target for this platform")
    if system == "windows" and architecture != "x86_64":
        raise ValueError("Windows standalone builds currently require x64")

    suffix = ".exe" if system == "windows" else ""
    return f"winix-local-{system}-{architecture}{suffix}"


def write_licenses(destination: Path) -> None:
    """Ship dependency notices alongside the bundled runtime."""
    notices = ["Winix Local bundled software notices\n"]
    for distribution in sorted(
        metadata.distributions(), key=lambda item: item.metadata["Name"].lower()
    ):
        for file in distribution.files or ():
            if file.name.lower().startswith(("license", "copying", "notice")):
                path = Path(str(distribution.locate_file(file)))
                if path.is_file():
                    notices.append(
                        f"\n--- {distribution.metadata['Name']}: {file} ---\n"
                    )
                    notices.append(path.read_text(encoding="utf-8", errors="replace"))

    license_candidates = (
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
    )
    python_license = next((path for path in license_candidates if path.is_file()), None)
    if python_license is None:
        raise ValueError("Python LICENSE.txt is required for the bundled interpreter")
    notices.extend(["\n--- Python ---\n", python_license.read_text(encoding="utf-8")])
    destination.write_text("\n".join(notices), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist/native"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = asset_name()
    licenses = output / (name + "-licenses.txt")
    write_licenses(licenses)

    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--console",
            "--noupx",
            "--name",
            name.removesuffix(".exe"),
            "--distpath",
            str(output),
            "--workpath",
            str(root / "build/standalone"),
            "--specpath",
            str(root / "build"),
            "--collect-data",
            "winix_local",
            "--add-data",
            f"{licenses}{os.pathsep}.",
            str(root / "src/tools/standalone_entry.py"),
        ],
        check=True,
    )
    print(output / name)


if __name__ == "__main__":
    main()
