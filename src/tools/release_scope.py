"""Choose release checks and verify unchanged setup downloads before reuse."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from winix_local.release import NATIVE_ASSETS

TOOL_INPUTS = {
    "LICENSE",
    "MANIFEST.in",
    ".github/workflows/standalone.yml",
    "src/tools/build_standalone.py",
    "src/tools/standalone_entry.py",
    "src/tools/check_standalone.py",
    "src/tools/check_wheel.py",
}
SINGLE_PLATFORM_TESTS = {
    "src/tests/integration/test_ha_runtime.py",
    "src/tests/unit/test_release_scope.py",
}
SETUP_DOWNLOADS = tuple(
    name for asset in NATIVE_ASSETS for name in (asset, asset + "-licenses.txt")
)


def git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *arguments], text=True, stderr=subprocess.PIPE
    ).strip()


def without_version(text: str) -> str:
    return re.sub(r'(?m)^version = "[^"\n]+"$', 'version = "VERSION"', text)


def tool_inputs_changed(root: Path, base: str) -> bool:
    """Unknown baselines require full checks. Only the package version is ignored."""
    if not re.fullmatch(r"(?:[0-9a-f]{40}|v[0-9]+\.[0-9]+\.[0-9]+)", base):
        return True
    try:
        changed = git(root, "diff", "--name-only", base, "HEAD", "--").splitlines()
        for name in changed:
            if (
                name in TOOL_INPUTS
                or name.startswith(("src/winix_local/", "src/launchers/"))
                or (name.startswith("src/tests/") and name not in SINGLE_PLATFORM_TESTS)
            ):
                return True
            if name == "pyproject.toml" and without_version(
                git(root, "show", base + ":pyproject.toml")
            ) != without_version(git(root, "show", "HEAD:pyproject.toml")):
                return True
    except subprocess.CalledProcessError:
        return True
    return False


def release_scope(root: Path, release: dict) -> dict:
    tag = release.get("tag_name", "")
    names = {asset["name"] for asset in release.get("assets", [])}
    reusable = (
        not release.get("draft")
        and not release.get("prerelease")
        and set((*SETUP_DOWNLOADS, "SHA256SUMS")).issubset(names)
        and not tool_inputs_changed(root, tag)
    )
    return {
        "native_changed": "false" if reusable else "true",
        "reuse_tag": tag if reusable else "",
    }


def reuse_downloads(source: Path, destination: Path) -> None:
    """Verify every required binary and notice before copying any of them."""
    checksums = {}
    for line in (source / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        digest, name = line.split("  ", 1)
        if name in checksums or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid or duplicate release checksum")
        checksums[name] = digest

    for name in SETUP_DOWNLOADS:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing setup download: " + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksums.get(name):
            raise ValueError("Checksum mismatch: " + name)
        if (destination / name).is_symlink():
            raise ValueError("Refusing to replace a symlink")

    destination.mkdir(parents=True, exist_ok=True)
    for name in SETUP_DOWNLOADS:
        shutil.copyfile(source / name, destination / name)
    print("Reused verified setup executables and license notices.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    validation = commands.add_parser("validation")
    validation.add_argument("--mode", choices=("auto", "all", "linux"), default="auto")
    validation.add_argument("--base-ref", default="")
    validation.add_argument("--output", type=Path, required=True)
    release = commands.add_parser("release")
    release.add_argument("--output", type=Path, required=True)
    reuse = commands.add_parser("reuse")
    reuse.add_argument("--source", type=Path, required=True)
    reuse.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "reuse":
        reuse_downloads(args.source, args.destination)
        return
    if args.command == "validation":
        full = args.mode == "all" or (
            args.mode == "auto"
            and tool_inputs_changed(args.project_root, args.base_ref)
        )
        values = {"full_matrix": str(full).lower()}
    else:
        repository = os.environ["GH_REPO"]
        try:
            previous = json.loads(
                subprocess.check_output(
                    ["gh", "api", f"repos/{repository}/releases/latest"],
                    text=True,
                    stderr=subprocess.PIPE,
                )
            )
        except subprocess.CalledProcessError:
            previous = {}
        values = release_scope(args.project_root, previous)

    with args.output.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")
            print(f"{key}={value}")


if __name__ == "__main__":
    main()
