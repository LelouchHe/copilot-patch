#!/usr/bin/env python3
"""Regression coverage for the ACP image-generation boot option."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parent.parent
PATCH = REPO / "patches" / "acp-image-generation.py"
SOURCE = (
    'function boot(e,t,n,r){let o=e.sandboxFlagOverride;'
    'return{clientKind:"acp",integrationId:"test",'
    "contextTier:e.options?.context??r?.contextTier??void 0,"
    "acpCliContextTier:e.options?.context,"
    "availableTools:e.options?.availableTools,"
    'excludedTools:e.options?.excludedTools,homeDirectory:"test"}}'
)


def load_patch():
    spec = importlib.util.spec_from_file_location("acp_image_generation", PATCH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load image patch")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AcpImageGenerationTests(unittest.TestCase):
    def test_enables_option_and_preserves_settings_opt_out(self):
        source = load_patch().build_patch(SOURCE)
        program = source + (
            "console.log(JSON.stringify(["
            "boot({options:{}},null,null,undefined),"
            "boot({options:{}},null,null,{imageGeneration:false}),"
            "boot({options:{context:'long_context',"
            "availableTools:['image_generation'],excludedTools:['bash']}},"
            "null,null,{imageGeneration:true})]));"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", program],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        default, disabled, enabled = json.loads(result.stdout)
        self.assertEqual(default["imageGeneration"], {"enabled": True})
        self.assertEqual(disabled["imageGeneration"], {"enabled": False})
        self.assertEqual(enabled["imageGeneration"], {"enabled": True})
        self.assertEqual(enabled["contextTier"], "long_context")
        self.assertEqual(enabled["availableTools"], ["image_generation"])
        self.assertEqual(enabled["excludedTools"], ["bash"])
        self.assertEqual(default["clientKind"], "acp")

    def test_is_idempotent(self):
        patch = load_patch()
        source = patch.build_patch(SOURCE)
        self.assertEqual(patch.build_patch(source), source)

    def test_handles_renamed_minified_settings_parameter(self):
        source = SOURCE.replace(",r)", ",settings)").replace("r?.", "settings?.")
        patched = load_patch().build_patch(source)
        self.assertIn("enabled:settings?.imageGeneration!==!1", patched)

    def test_refuses_missing_or_ambiguous_anchor(self):
        patch = load_patch()
        for source in ("const untouched=true;", SOURCE + SOURCE):
            with self.subTest(source=source):
                with self.assertRaises(LookupError):
                    patch.build_patch(source)

    def test_refuses_existing_unrecognized_image_option(self):
        source = SOURCE.replace(
            'clientKind:"acp",',
            'clientKind:"acp",imageGeneration:{enabled:false},',
        )
        with self.assertRaises(LookupError):
            load_patch().build_patch(source)

    def test_refuses_incomplete_marker(self):
        with self.assertRaises(LookupError):
            load_patch().build_patch("/*copilot-patch:acp-image-generation*/" + SOURCE)

    def run_patch(self, source, expected_code):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "cli-main.mjs"
        path.write_text(source)
        result = subprocess.run(
            [sys.executable, str(PATCH), str(path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, expected_code, result.stderr)
        return path, result

    def test_standalone_patch_preserves_backup_and_is_idempotent(self):
        path, _ = self.run_patch(SOURCE, 0)
        self.assertEqual(Path(str(path) + ".orig").read_text(), SOURCE)
        patched = path.read_text()
        load_patch().main(["patch", str(path)])
        self.assertEqual(path.read_text(), patched)
        self.assertEqual(Path(str(path) + ".orig").read_text(), SOURCE)

    def test_anchor_failure_does_not_change_file(self):
        source = "const untouched=true;"
        path, result = self.run_patch(source, 2)
        self.assertIn("anchor", result.stderr)
        self.assertEqual(path.read_text(), source)
        self.assertFalse(Path(str(path) + ".orig").exists())

    def test_invalid_javascript_does_not_change_file(self):
        source = SOURCE + "const = ;"
        path, result = self.run_patch(source, 2)
        self.assertIn("validation failed", result.stderr)
        self.assertEqual(path.read_text(), source)
        self.assertFalse(Path(str(path) + ".orig").exists())
        self.assertEqual(list(path.parent.glob("*.patching.*")), [])


if __name__ == "__main__":
    unittest.main()
