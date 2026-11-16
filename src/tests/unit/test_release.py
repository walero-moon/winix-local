"""Release gates and checksums; no publishing or network access."""

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from winix_local.build import BuildError
from winix_local.release import NATIVE_ASSETS, prepare_release

ROOT = Path(__file__).resolve().parents[3]


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        for directory in ("src/winix_local", "src/home_assistant/winix_local"):
            shutil.copytree(ROOT / directory, self.project / directory)
        for name in ("pyproject.toml", "manifest.json", "hacs.json", "LICENSE"):
            shutil.copyfile(ROOT / name, self.project / name)
        manifest = json.loads((self.project / "manifest.json").read_text())
        manifest["version"] = "0.1.0"
        (self.project / "manifest.json").write_text(json.dumps(manifest))
        project = self.project / "pyproject.toml"
        project.write_text(
            project.read_text().replace('version = "0.1.1"', 'version = "0.1.0"')
        )
        (self.project / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [0.1.0] - 2026-11-16\n\nFirst experimental release.\n\n"
            "## [0.0.1] - 2026-11-01\n\nOlder notes.\n"
        )
        self.output = self.project / "dist"
        self.output.mkdir()
        for name in ("winix_local-0.1.0-py3-none-any.whl", "winix_local-0.1.0.tar.gz"):
            (self.output / name).write_bytes(b"fixture artifact")

    def test_release_contains_exact_hashes_and_only_its_own_notes(self):
        artifacts = prepare_release(self.project, "v0.1.0")
        self.assertEqual(len(artifacts), 4)
        for line, path in zip(
            (self.output / "SHA256SUMS").read_text().splitlines(), artifacts[:3]
        ):
            self.assertEqual(
                line, f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
            )
        notes = (self.output / "release-notes.md").read_text()
        self.assertIn("First experimental release.", notes)
        self.assertNotIn("Older notes", notes)

    def test_bad_tag_never_replaces_previous_artifact(self):
        archive = self.output / "winix_local.zip"
        archive.write_bytes(b"previous ZIP")
        for tag in ("v0.2.0", "0.1.0", "v0.1.0-extra"):
            with self.subTest(tag=tag), self.assertRaises(BuildError):
                prepare_release(self.project, tag)
            self.assertEqual(archive.read_bytes(), b"previous ZIP")

    def test_missing_notes_stop_release(self):
        (self.project / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [0.2.0]\nNext version.\n"
        )
        with self.assertRaisesRegex(BuildError, "release notes"):
            prepare_release(self.project, "v0.1.0")
        self.assertFalse((self.output / "winix_local.zip").exists())

    def test_missing_distribution_stops_release(self):
        (self.output / "winix_local-0.1.0.tar.gz").unlink()
        with self.assertRaisesRegex(BuildError, "wheel and source"):
            prepare_release(self.project, "v0.1.0")

    def test_python_version_mismatch_stops_release(self):
        manifest = json.loads((self.project / "manifest.json").read_text())
        manifest["version"] = "0.2.0"
        (self.project / "manifest.json").write_text(json.dumps(manifest))
        for name in ("winix_local-0.2.0-py3-none-any.whl", "winix_local-0.2.0.tar.gz"):
            (self.output / name).write_bytes(b"fixture")
        (self.project / "CHANGELOG.md").write_text("## [0.2.0]\n\nNext release.\n")
        with self.assertRaisesRegex(BuildError, "version"):
            prepare_release(self.project, "v0.2.0")
        self.assertFalse((self.output / "winix_local.zip").exists())

    def test_native_release_requires_every_target_and_stamps_pinned_launchers(self):
        with self.assertRaisesRegex(BuildError, "all native"):
            prepare_release(self.project, "v0.1.0", with_standalone=True)

        shutil.copytree(ROOT / "src/launchers", self.project / "src/launchers")
        for asset in NATIVE_ASSETS:
            for name in (asset, asset + "-licenses.txt"):
                (self.output / name).write_bytes(b"fixture native file")
        artifacts = prepare_release(self.project, "v0.1.0", with_standalone=True)
        self.assertEqual(len(artifacts), 16)
        for launcher in ("setup.sh", "setup.ps1"):
            text = (self.output / launcher).read_text()
            self.assertIn("v0.1.0", text)
            self.assertNotIn("__RELEASE_TAG__", text)
        hashes = (self.output / "SHA256SUMS").read_text()
        for path in artifacts[:-1]:
            self.assertIn(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n",
                hashes,
            )
