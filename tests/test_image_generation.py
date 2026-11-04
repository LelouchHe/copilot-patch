#!/usr/bin/env python3
"""Exercise the image-generation launcher through a stand-in Copilot process."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "bin" / "copilot-acp.sh"
FLAG = "IMAGE_GENERATION_TOOL"


class ImageGenerationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        fake = self.root / "copilot"
        fake.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "print(json.dumps({'args': sys.argv[1:], "
            "'enabled': os.environ.get('COPILOT_CLI_ENABLED_FEATURE_FLAGS'), "
            "'disabled': os.environ.get('COPILOT_CLI_DISABLED_FEATURE_FLAGS'), "
            "'cwd': os.getcwd()}))\n"
            "sys.exit(int(os.environ.get('FAKE_COPILOT_EXIT', '0')))\n"
        )
        fake.chmod(0o755)
        pipeline = self.root / "python3"
        pipeline.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$@\" >\"$FAKE_PIPELINE_LOG\"\n"
            "exit 0\n"
        )
        pipeline.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(self.root) + os.pathsep + os.environ.get("PATH", ""),
            "FAKE_PIPELINE_LOG": str(self.root / "pipeline.log"),
        }
        for key in (
            "COPILOT_CLI_ENABLED_FEATURE_FLAGS",
            "COPILOT_CLI_DISABLED_FEATURE_FLAGS",
            "FAKE_COPILOT_EXIT",
        ):
            self.env.pop(key, None)

    def launch(self, *args, launcher=LAUNCHER):
        result = subprocess.run(
            ["/bin/bash", str(launcher), *args],
            env=self.env,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def test_enables_image_tool_and_passes_acp_arguments(self):
        code, result = self.launch("--acp", "--context", "long_context")
        self.assertEqual(code, 0)
        self.assertEqual(result["enabled"], FLAG)
        self.assertEqual(result["args"], ["--acp", "--context", "long_context"])
        self.assertTrue(self.root.samefile(result["cwd"]))

    def test_supports_non_acp_prompts_without_changing_arguments(self):
        _, result = self.launch("-p", "Generate a solar system", "--silent")
        self.assertEqual(result["enabled"], FLAG)
        self.assertEqual(
            result["args"], ["-p", "Generate a solar system", "--silent"]
        )

    def test_preserves_other_enabled_flags(self):
        self.env["COPILOT_CLI_ENABLED_FEATURE_FLAGS"] = "CATALOG_SEARCH,OTHER"
        _, result = self.launch()
        self.assertEqual(result["enabled"], "CATALOG_SEARCH,OTHER," + FLAG)

    def test_does_not_duplicate_existing_flag(self):
        for flags in (FLAG, FLAG + ",OTHER", "OTHER," + FLAG + ",LAST"):
            with self.subTest(flags=flags):
                self.env["COPILOT_CLI_ENABLED_FEATURE_FLAGS"] = flags
                _, result = self.launch()
                self.assertEqual(result["enabled"], flags)

    def test_similar_flag_name_does_not_count_as_enabled(self):
        self.env["COPILOT_CLI_ENABLED_FEATURE_FLAGS"] = FLAG + "_OTHER"
        _, result = self.launch()
        self.assertEqual(result["enabled"], FLAG + "_OTHER," + FLAG)

    def test_preserves_disabled_flags(self):
        self.env["COPILOT_CLI_DISABLED_FEATURE_FLAGS"] = "OTHER," + FLAG
        _, result = self.launch()
        self.assertEqual(result["disabled"], "OTHER," + FLAG)

    def test_forwards_exit_status(self):
        self.env["FAKE_COPILOT_EXIT"] = "23"
        code, _ = self.launch()
        self.assertEqual(code, 23)

    def test_runs_patch_pipeline_before_copilot(self):
        _, result = self.launch("--version")
        self.assertEqual(result["enabled"], FLAG)
        self.assertEqual(result["args"], ["--version"])
        self.assertEqual(
            (self.root / "pipeline.log").read_text().splitlines(),
            [str(REPO / "lib" / "apply_patches.py")],
        )


if __name__ == "__main__":
    unittest.main()
