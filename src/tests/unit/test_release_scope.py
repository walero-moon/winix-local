"""Check release scope decisions and verified reuse without network access."""

import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.release_scope import (
    SETUP_DOWNLOADS,
    release_scope,
    reuse_downloads,
    tool_inputs_changed,
)


class ReleaseScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run_git("init", "--quiet")
        self.write(
            "pyproject.toml",
            '[project]\nversion = "0.1.3"\ndependencies = ["cryptography>=43"]\n',
        )
        self.write("src/winix_local/cli.py", "original CLI\n")
        self.write("src/home_assistant/winix_local/strings.json", "{}\n")
        self.commit()
        self.base = self.run_git("rev-parse", "HEAD")
        self.run_git("tag", "v0.1.3")

    def run_git(self, *arguments):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *arguments],
            text=True,
            stderr=subprocess.PIPE,
        ).strip()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def commit(self):
        self.run_git("add", ".")
        self.run_git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        )

    def test_ha_and_version_changes_can_reuse_setup_downloads(self):
        project = self.root / "pyproject.toml"
        project.write_text(project.read_text().replace('"0.1.3"', '"0.1.5"'))
        self.write(
            "src/home_assistant/winix_local/strings.json", '{"title":"New UI"}\n'
        )
        self.write("README.md", "New instructions\n")
        self.commit()
        self.assertFalse(tool_inputs_changed(self.root, self.base))
        previous = {
            "tag_name": "v0.1.3",
            "assets": [{"name": name} for name in (*SETUP_DOWNLOADS, "SHA256SUMS")],
        }
        self.assertEqual(
            release_scope(self.root, previous),
            {
                "native_changed": "false",
                "reuse_tag": "v0.1.3",
            },
        )
        previous["assets"].pop()
        self.assertEqual(release_scope(self.root, previous)["native_changed"], "true")

    def test_runtime_dependency_and_build_changes_require_full_checks(self):
        changes = (
            ("src/winix_local/cli.py", "changed CLI\n"),
            (
                "pyproject.toml",
                '[project]\nversion = "0.1.5"\ndependencies = ["cryptography>=46"]\n',
            ),
            ("src/tools/build_standalone.py", "changed builder\n"),
            ("src/launchers/setup.sh", "changed launcher\n"),
            (".github/workflows/standalone.yml", "changed build environment\n"),
        )
        for name, content in changes:
            with self.subTest(name=name):
                self.write(name, content)
                self.commit()
                self.assertTrue(tool_inputs_changed(self.root, self.base))
                self.run_git("reset", "--hard", self.base)
        self.assertTrue(tool_inputs_changed(self.root, ""))
        self.assertTrue(tool_inputs_changed(self.root, "0" * 40))

    def test_download_reuse_verifies_every_file_before_copying(self):
        source = self.root / "downloads"
        source.mkdir()
        checksums = []
        for name in SETUP_DOWNLOADS:
            data = name.encode()
            (source / name).write_bytes(data)
            checksums.append(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
        (source / "SHA256SUMS").write_text("".join(checksums))
        (source / SETUP_DOWNLOADS[-1]).write_bytes(b"tampered")
        destination = self.root / "dist"
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            reuse_downloads(source, destination)
        self.assertFalse(destination.exists())
        (source / SETUP_DOWNLOADS[-1]).write_bytes(SETUP_DOWNLOADS[-1].encode())
        reuse_downloads(source, destination)
        self.assertEqual(
            {path.name for path in destination.iterdir()}, set(SETUP_DOWNLOADS)
        )
