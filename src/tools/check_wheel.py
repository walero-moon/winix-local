"""Install a release wheel without source builds and exercise its installed CLI."""

import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import venv


def check_wheel(wheel_directory: Path) -> None:
    wheels = list(Path(wheel_directory).glob("winix_local-*-py3-none-any.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected exactly one Winix Local wheel")
    wheel = wheels[0].resolve()

    # Keep the consumer installation outside the checkout. Neither editable
    # installs nor PYTHONPATH should make an incomplete wheel appear usable.
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    with tempfile.TemporaryDirectory(prefix="winix-wheel-install-") as temporary:
        root = Path(temporary)
        destination = root / "venv"
        venv.EnvBuilder(with_pip=True).create(destination)
        scripts = destination / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")

        def run(arguments):
            subprocess.run(
                [str(python), "-I", *arguments],
                cwd=root,
                env=environment,
                check=True,
                timeout=300,
            )

        run(
            [
                "-m",
                "pip",
                "install",
                "--only-binary=:all:",
                "--no-cache-dir",
                str(wheel),
            ]
        )
        run(["-m", "pip", "check"])
        run(["-m", "winix_local", "--help"])
        run(["-m", "winix_local", "setup", "--help"])
        run(
            [
                "-c",
                "from pathlib import Path; import sys, winix_local; "
                "from winix_local.discovery import identify_device; "
                "from winix_local.model import load_builtin_catalog; "
                "assert Path(winix_local.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
                "assert callable(identify_device); "
                "assert load_builtin_catalog().model_ids",
            ]
        )
        for name in ("winix-local", "winix-local-broker", "winix-local-build"):
            executable = scripts / (name + ".exe" if os.name == "nt" else name)
            subprocess.run(
                [str(executable), "--help"],
                cwd=root,
                env=environment,
                check=True,
                timeout=30,
            )

    print("Wheel installation passed without source builds or a project checkout.")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        check_wheel(args.wheel_dir)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Wheel installation check failed: {error}\n")


if __name__ == "__main__":
    main()
