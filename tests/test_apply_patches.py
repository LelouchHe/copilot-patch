#!/usr/bin/env python3
"""Tests for the staged patch pipeline."""

import contextlib
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

import apply_patches  # noqa: E402
from apply_patches import apply_pipeline  # noqa: E402


class PatchPipelineTests(unittest.TestCase):
    def make_patch(self, directory: Path, name: str, body: str) -> Path:
        path = directory / name
        path.write_text("#!/bin/bash\nset -e\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def run_pipeline(self, app: Path, patches: Path):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            result = apply_pipeline(str(app), str(patches))
        return result, output.getvalue()

    def test_failed_patch_is_discarded_and_later_patch_runs(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            app = root / "app.js"
            app.write_text("const base = true;\n")
            patches = root / "patches"
            patches.mkdir()
            self.make_patch(
                patches,
                "10-first.sh",
                'printf "\\nconst first = true;\\n" >>"$1"\n',
            )
            self.make_patch(patches, "20-fails.sh", 'echo broken >>"$1"\nexit 7\n')
            self.make_patch(
                patches,
                "30-last.sh",
                'printf "\\nconst last = true;\\n" >>"$1"\n',
            )

            result, output = self.run_pipeline(app, patches)

            self.assertEqual(result.applied, ["10-first.sh", "30-last.sh"])
            self.assertEqual(result.failed, ["20-fails.sh"])
            self.assertIn("const first = true", app.read_text())
            self.assertNotIn("broken", app.read_text())
            self.assertIn("const last = true", app.read_text())
            self.assertIn("APPLIED  10-first.sh", output)
            self.assertIn("FAILED   20-fails.sh", output)

    def test_each_run_rebuilds_from_pristine_original(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            app = root / "app.js"
            app.write_text("const base = true;\n")
            patches = root / "patches"
            patches.mkdir()
            patch = self.make_patch(
                patches,
                "10-extra.sh",
                'printf "\\nconst extra = true;\\n" >>"$1"\n',
            )

            self.run_pipeline(app, patches)
            self.assertIn("const extra = true", app.read_text())

            patch.unlink()
            self.run_pipeline(app, patches)
            self.assertEqual(app.read_text(), "const base = true;\n")

    def test_invalid_final_stage_preserves_previous_live_bundle(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            app = root / "app.js"
            app.write_text("const live = true;\n")
            (root / "app.js.orig").write_text("const base = true;\n")
            patches = root / "patches"
            patches.mkdir()
            self.make_patch(
                patches,
                "10-valid.sh",
                'printf "\\nconst patched = true;\\n" >>"$1"\n',
            )

            checks = 0

            def fail_final(_path):
                nonlocal checks
                checks += 1
                return (checks == 1, "injected final validation failure")

            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                result = apply_pipeline(str(app), str(patches), fail_final)

            self.assertFalse(result.published)
            self.assertEqual(app.read_text(), "const live = true;\n")
            self.assertIn("RESULT   publish failed", output.getvalue())

    def test_original_backup_creation_is_atomic_on_copy_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            app = root / "app.js"
            app.write_text("const live = true;\n")
            original = root / "app.js.orig"

            def partial_copy(_source, destination):
                Path(destination).write_text("partial")
                raise OSError("simulated copy failure")

            with mock.patch.object(apply_patches.shutil, "copy2", partial_copy):
                with self.assertRaises(OSError):
                    apply_patches.ensure_original(app, original)

            self.assertFalse(original.exists())
            self.assertEqual(
                list(root.glob("app.js.orig.creating.*")),
                [],
                "failed temporary backup must be removed",
            )

    def test_missing_node_preserves_live_bundle(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            app = root / "app.js"
            app.write_text("const live = true;\n")
            (root / "app.js.orig").write_text("const base = true;\n")
            patches = root / "patches"
            patches.mkdir()

            with mock.patch.object(apply_patches.shutil, "which", return_value=None):
                result, output = self.run_pipeline(app, patches)

            self.assertFalse(result.published)
            self.assertEqual(app.read_text(), "const live = true;\n")
            self.assertIn("node is required", output)

    def test_patch_exec_error_is_skipped_and_later_patch_runs(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            app = root / "app.js"
            app.write_text("const base = true;\n")
            patches = root / "patches"
            patches.mkdir()
            broken = patches / "10-broken.py"
            broken.write_text("#!/definitely/missing\n")
            broken.chmod(broken.stat().st_mode | stat.S_IXUSR)
            self.make_patch(
                patches,
                "20-good.sh",
                'printf "\\nconst good = true;\\n" >>"$1"\n',
            )

            result, output = self.run_pipeline(app, patches)

            self.assertEqual(result.failed, ["10-broken.py"])
            self.assertEqual(result.applied, ["20-good.sh"])
            self.assertIn("const good = true", app.read_text())
            self.assertIn("FAILED   10-broken.py", output)


if __name__ == "__main__":
    unittest.main()
