"""Release packaging acceptance tests; no Home Assistant or network required."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from winix_local.build import ADAPTER_FILES, CORE_FILES, BuildError, build_archive, main

ROOT = Path(__file__).resolve().parents[3]


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"

        for directory in ("src/winix_local", "src/home_assistant/winix_local"):
            shutil.copytree(ROOT / directory, self.project / directory)
        for filename in ("pyproject.toml", "hacs.json", "manifest.json", "LICENSE"):
            shutil.copyfile(ROOT / filename, self.project / filename)

    def update_json(self, relative_path, **fields):
        path = self.project / relative_path
        document = json.loads(path.read_text())
        document.update(fields)
        path.write_text(json.dumps(document))

    def test_archive_has_installable_root_and_only_runtime_files(self):
        # Private bundles or editor debris beside source must not leak into ZIPs.
        for filename in (
            "src/home_assistant/winix_local/device.key",
            "src/home_assistant/winix_local/bundle.json",
            "src/winix_local/ca.key",
            ".env",
        ):
            (self.project / filename).write_text("fixture-private-material")

        archive_path = build_archive(self.project)
        with zipfile.ZipFile(archive_path) as archive:
            expected = set(ADAPTER_FILES) | {f"_core/{name}" for name in CORE_FILES}
            expected.update({"manifest.json", "LICENSE"})
            self.assertEqual(set(archive.namelist()), expected)
            self.assertIsNone(archive.testzip())
            self.assertEqual(
                json.loads(archive.read("manifest.json"))["domain"], "winix_local"
            )
            self.assertNotIn(
                b"fixture-private-material",
                b"".join(archive.read(name) for name in archive.namelist()),
            )
            self.assertFalse(
                any(
                    name.startswith("custom_components/") for name in archive.namelist()
                )
            )

    def test_installed_archive_loads_core_without_checkout_or_installed_library(self):
        archive_path = build_archive(self.project)
        config_directory = Path(self.temporary.name) / "config"
        integration_directory = config_directory / "custom_components/winix_local"
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(integration_directory)

        # -I ignores PYTHONPATH and the checkout; only the extracted HA config is
        # added explicitly. This reproduces HACS's destination directory layout.
        script = """
import sys
sys.path.insert(0, sys.argv[1])
from custom_components.winix_local._core.model import load_builtin_catalog
from custom_components.winix_local._core.wire import device_id, topic
assert len(load_builtin_catalog().model_ids) == 59
assert topic('Control', device_id('AABBCCDDEEFF', 'TESTTOKEN1')) == 'Winix/Control/Air01/5510_US/AABBCCDDEEFF_TESTTOKEN1'
assert 'winix_local' not in sys.modules
assert not any(name.startswith('homeassistant') for name in sys.modules)
print('isolated archive imports passed')
"""
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(config_directory)],
            cwd=self.temporary.name,
            text=True,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("isolated archive imports passed", result.stdout)

    def test_repeated_builds_ignore_source_mtimes_and_permissions(self):
        first = build_archive(self.project, "first/winix_local.zip").read_bytes()
        source = self.project / "src/winix_local/wire.py"
        os.utime(source, (1700000000, 1700000000))
        source.chmod(0o444)

        second = build_archive(self.project, "second/winix_local.zip").read_bytes()
        self.assertEqual(first, second)

    def test_metadata_mismatch_never_replaces_previous_artifact(self):
        output = build_archive(self.project)
        previous = output.read_bytes()
        self.update_json("manifest.json", version="99.0.0")

        with self.assertRaisesRegex(BuildError, "domain/version"):
            build_archive(self.project)
        self.assertEqual(output.read_bytes(), previous)

    def test_hacs_configuration_and_static_project_metadata_are_required(self):
        for field, value in (
            ("content_in_root", False),
            ("zip_release", False),
            ("hide_default_branch", False),
            ("filename", "wrong.zip"),
        ):
            with self.subTest(field=field):
                original = (self.project / "hacs.json").read_text()
                self.update_json("hacs.json", **{field: value})
                with self.assertRaisesRegex(BuildError, "hacs.json"):
                    build_archive(self.project)
                (self.project / "hacs.json").write_text(original)

        pyproject = self.project / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "winix-local"\nversion = "0.1.0"\nversion = "0.2.0"\n'
        )
        with self.assertRaisesRegex(BuildError, "one static project.version"):
            build_archive(self.project)

    def test_missing_runtime_module_and_source_links_fail_before_output(self):
        source = self.project / "src/winix_local/wire.py"
        source.unlink()
        with self.assertRaisesRegex(BuildError, "missing"):
            build_archive(self.project)
        self.assertFalse((self.project / "dist").exists())

        source.symlink_to(ROOT / "src/winix_local/wire.py")
        with self.assertRaisesRegex(BuildError, "symlinks"):
            build_archive(self.project)
        self.assertFalse((self.project / "dist").exists())

    def test_unlisted_python_module_is_not_silently_omitted(self):
        (self.project / "src/home_assistant/winix_local/new_runtime.py").write_text(
            "pass\n"
        )
        with self.assertRaisesRegex(BuildError, "allowlist"):
            build_archive(self.project)

    def test_build_cannot_overwrite_source_or_follow_output_links(self):
        for output in ("src/winix_local/winix_local.zip", "dist/wrong.zip"):
            with self.subTest(output=output), self.assertRaises(BuildError):
                build_archive(self.project, output)

        destination = Path(self.temporary.name) / "elsewhere"
        destination.mkdir()
        (self.project / "dist").symlink_to(destination, target_is_directory=True)
        with self.assertRaisesRegex(BuildError, "symlinks"):
            build_archive(self.project)
        self.assertEqual(list(destination.iterdir()), [])

    def test_command_reports_artifact_and_checksum(self):
        output = io.StringIO()
        with redirect_stdout(output):
            main(["--project-root", str(self.project)])

        self.assertIn("winix_local.zip", output.getvalue())
        self.assertRegex(output.getvalue(), r"SHA256 [0-9a-f]{64}")


if __name__ == "__main__":
    unittest.main()
