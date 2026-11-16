"""Prepare release files after validation; GitHub publishing stays in the workflow."""

import argparse
import hashlib
import json
from pathlib import Path
import re

from .build import ARCHIVE_NAME, BuildError, build_archive


def prepare_release(project_root: Path, tag: str) -> tuple[Path, ...]:
    """Require matching versions and release notes before preparing any upload."""
    project_root = Path(project_root)
    manifest = json.loads((project_root / "manifest.json").read_text())
    version = manifest["version"]
    if tag != "v" + version:
        raise BuildError("Release tag must match the manifest and Python version")

    changelog = (project_root / "CHANGELOG.md").read_text()
    section = re.search(
        rf"(?ms)^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)", changelog
    )
    if section is None or not section.group(1).strip():
        raise BuildError("Add release notes for this version to CHANGELOG.md")

    output = project_root / "dist"
    artifacts = (
        output / ARCHIVE_NAME,
        output / f"winix_local-{version}-py3-none-any.whl",
        output / f"winix_local-{version}.tar.gz",
    )
    for path in artifacts[1:]:
        if path.is_symlink() or not path.is_file():
            raise BuildError("Build the wheel and source distribution before release")
    for name in ("release-notes.md", "SHA256SUMS"):
        if (output / name).is_symlink():
            raise BuildError("Release output must not be a symlink")

    # The ZIP builder checks all project versions and the HACS metadata as well.
    build_archive(project_root)
    (output / "release-notes.md").write_text(
        section.group(1).strip() + "\n", encoding="utf-8"
    )
    checksums = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in artifacts
    )
    checksum_path = output / "SHA256SUMS"
    checksum_path.write_text(checksums, encoding="ascii")
    return (*artifacts, checksum_path)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--tag", required=True)
    args = parser.parse_args(argv)

    try:
        artifacts = prepare_release(args.project_root, args.tag)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Release preparation failed: {error}\n")

    for path in artifacts:
        print(path.name)


if __name__ == "__main__":
    main()
