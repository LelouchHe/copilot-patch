#!/usr/bin/env python3
"""Tests for ACP context-tier patch compatibility."""

import importlib.util
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = os.path.join(REPO, "patches", "acp-context-tier.py")


def load_patch():
    spec = importlib.util.spec_from_file_location("acp_context_tier", PATCH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load patch module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


OLD_SOURCE = (
    "async resolveInitialReasoningEffort(){"
    "return(await Gt.load(this.options.settings))?.effortLevel}"
    'create({clientKind:"acp",reasoningEffort:e});'
    "this.wirePermissionHandling(s,e);"
    "async applySessionModel(s,e){"
    "await s.session.model.switchTo({modelId:e})}"
)

NATIVE_SOURCE = (
    "async resolveInitialReasoningEffort(){"
    "return(await y.userSettingsLoad({"
    "configDir:this.options.settings?.configDir,"
    "homeDirectory:SM.homedir(),environment:process.env"
    "}))?.effortLevel}"
    'create({clientKind:"acp",reasoningEffort:e});'
    "this.wirePermissionHandling(s,e);"
    "async applySessionModel(s,e){"
    'await s.session.model.switchTo({modelId:e,source:"sdk"})}'
)


class ContextTierPatchTests(unittest.TestCase):
    def assert_patched(self, source):
        patch = load_patch()
        result, stats = patch.build_patch(source)
        self.assertIn("async __acpContextTier()", result)
        self.assertIn("contextTier:await this.__acpContextTier()", result)
        self.assertIn("await this.__acpApplyTier(s)", result)
        self.assertIn("contextTier:__t", result)
        self.assertEqual(stats["session sites"], 1)
        self.assertEqual(stats["force-apply"], 1)
        self.assertEqual(stats["model-switch"], 1)

    def test_patches_legacy_settings_loader(self):
        self.assert_patched(OLD_SOURCE)

    def test_patches_native_settings_loader(self):
        self.assert_patched(NATIVE_SOURCE)


if __name__ == "__main__":
    unittest.main()
