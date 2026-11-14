"""Build the HACS integration ZIP from the project's single source tree.

HACS extracts release ZIPs directly into custom_components/winix_local. This
archive therefore contains the integration files at its root, plus a private
_core package assembled from the shared Python library. Building never imports
Home Assistant, reads installation bundles, or contacts a network service.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import tempfile
from typing import Optional, Union
import zipfile

DOMAIN = "winix_local"
ARCHIVE_NAME = "winix_local.zip"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

# Explicit lists keep generated credentials, local configuration, tests and CLI
# tooling out of the HA runtime artifact. Add new runtime modules deliberately.
CORE_FILES = (
    "__init__.py",
    "backend.py",
    "certificate_setup.py",
    "catalog.json",
    "const.py",
    "installation.py",
    "model.py",
    "offline_clock.py",
    "pki.py",
    "private_storage.py",
    "setup_protocol.py",
    "wire.py",
)
ADAPTER_FILES = (
    "__init__.py",
    "config_flow.py",
    "coordinator.py",
    "downloads.py",
    "entity.py",
    "fan.py",
    "runtime.py",
    "sensor.py",
    "strings.json",
    "switch.py",
    "translations/en.json",
)
CORE_TOOLING_FILES = {
    "discovery.py",
    "networks.py",
    "setup.py",
    "setup_checks.py",
    "setup_files.py",
    "build.py",
    "prepare_broker.py",
    "provision.py",
    "cli.py",
    "__main__.py",
}


class BuildError(ValueError):
    """Source layout or package metadata cannot produce a valid HACS artifact."""


def _source_file(project_root: Path, relative_path: str) -> Path:
    """Require a regular source file without following project-internal links."""
    path = project_root
    for part in Path(relative_path).parts:
        path = path / part
        if path.is_symlink():
            raise BuildError(f"Source symlinks are not supported: {relative_path}")

    if not path.is_file():
        raise BuildError(f"Required source file is missing: {relative_path}")

    return path


def _read_json(project_root: Path, relative_path: str) -> dict:
    try:
        document = json.loads(_source_file(project_root, relative_path).read_text())
    except json.JSONDecodeError as error:
        raise BuildError(f"Invalid JSON in {relative_path}") from error

    if not isinstance(document, dict):
        raise BuildError(f"Expected an object in {relative_path}")

    return document


def _project_version(project_root: Path) -> str:
    """Read this project's static PEP 621 fields without a TOML dependency.

    Python 3.9 lacks tomllib. Accept exactly one static name/version assignment
    in one [project] table; reject unsupported or ambiguous forms explicitly.
    """
    source = _source_file(project_root, "pyproject.toml").read_text()
    tables = re.findall(r"(?ms)^\[project\][ \t]*\n(.*?)(?=^\[|\Z)", source)
    if len(tables) != 1:
        raise BuildError("pyproject.toml must contain one [project] table")

    fields = {}
    for name in ("name", "version"):
        assignments = [
            line
            for line in tables[0].splitlines()
            if re.match(rf"^\s*{name}\s*=", line)
        ]
        if len(assignments) != 1:
            raise BuildError(f"pyproject.toml requires one static project.{name}")

        match = re.fullmatch(
            rf"\s*{name}\s*=\s*([\"'])([^\"'\r\n]+)\1\s*(?:#.*)?",
            assignments[0],
        )
        if match is None:
            raise BuildError(f"Unsupported project.{name}; use a quoted static value")

        fields[name] = match.group(2)

    if fields["name"] != "winix-local":
        raise BuildError("Expected project.name = 'winix-local'")

    return fields["version"]


def _validate_metadata(project_root: Path) -> None:
    version = _project_version(project_root)
    manifest = _read_json(project_root, "manifest.json")
    hacs = _read_json(project_root, "hacs.json")

    if manifest.get("domain") != DOMAIN or manifest.get("version") != version:
        raise BuildError("Integration domain/version must match the Python project")

    for field in ("content_in_root", "zip_release", "hide_default_branch"):
        if hacs.get(field) is not True:
            raise BuildError(f"hacs.json must set {field} to true")

    if hacs.get("filename") != ARCHIVE_NAME:
        raise BuildError(f"hacs.json filename must be {ARCHIVE_NAME}")


def _reject_unlisted_modules(project_root: Path) -> None:
    for directory, allowed in (
        ("src/winix_local", set(CORE_FILES) | CORE_TOOLING_FILES),
        ("src/home_assistant/winix_local", set(ADAPTER_FILES)),
    ):
        for path in (project_root / directory).rglob("*.py"):
            relative_path = path.relative_to(project_root / directory).as_posix()
            if relative_path not in allowed:
                raise BuildError(
                    f"Runtime module is not in the build allowlist: {directory}/{relative_path}"
                )


def _archive_contents(project_root: Path) -> dict[str, bytes]:
    _validate_metadata(project_root)
    _reject_unlisted_modules(project_root)

    sources = {name: f"src/home_assistant/winix_local/{name}" for name in ADAPTER_FILES}
    sources.update({f"_core/{name}": f"src/winix_local/{name}" for name in CORE_FILES})
    sources.update({"manifest.json": "manifest.json", "LICENSE": "LICENSE"})

    return {
        archive_path: _source_file(project_root, relative_path).read_bytes()
        for archive_path, relative_path in sorted(sources.items())
    }


def _output_path(project_root: Path, output: Optional[Union[str, Path]]) -> Path:
    output_path = (
        Path(output) if output is not None else project_root / "dist" / ARCHIVE_NAME
    )
    if not output_path.is_absolute():
        output_path = project_root / output_path

    # Fail before replacing anything if a caller points output at source or a
    # symlink. A build artifact must never overwrite the implementation it uses.
    for path in (output_path, *output_path.parents):
        if path.is_symlink():
            raise BuildError("Output symlinks are not supported")

    output_path = output_path.resolve()
    source_directory = project_root / "src"
    if source_directory == output_path or source_directory in output_path.parents:
        raise BuildError("Build output must be outside src/")
    if output_path.name != ARCHIVE_NAME:
        raise BuildError(f"Output filename must match hacs.json: {ARCHIVE_NAME}")

    return output_path


def build_archive(
    project_root: Union[str, Path], output: Optional[Union[str, Path]] = None
) -> Path:
    """Assemble a deterministic, self-contained ZIP; never publish or deploy it."""
    project_root = Path(project_root)
    if project_root.is_symlink():
        raise BuildError("Project root must not be a symlink")
    project_root = project_root.resolve()

    contents = _archive_contents(project_root)
    output_path = _output_path(project_root, output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write beside the destination and replace only after the ZIP is complete.
    # Stable entry order, timestamps and modes make repeat builds byte-identical.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)

        with zipfile.ZipFile(
            temporary_path, "w", compression=zipfile.ZIP_STORED
        ) as archive:
            for name, data in contents.items():
                entry = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIMESTAMP)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(entry, data)

        temporary_path.replace(output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return output_path


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output", type=Path, help=f"Output path, named {ARCHIVE_NAME}"
    )
    args = parser.parse_args(argv)

    try:
        archive_path = build_archive(args.project_root, args.output)
    except (BuildError, OSError) as error:
        parser.exit(1, f"Build failed: {error}\n")

    print(f"Built {archive_path}")
    print(f"SHA256 {hashlib.sha256(archive_path.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
