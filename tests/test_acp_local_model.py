#!/usr/bin/env python3
"""Tests for the ACP local-model presentation patch."""

import importlib.util
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = os.path.join(REPO, "patches", "acp-local-model.py")


def load_patch():
    spec = importlib.util.spec_from_file_location("acp_local_model", PATCH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load patch module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SOURCE = (
    'async buildConfigOptions(e,n){let r=[];'
    'r.push(this.createSelectOption({id:"mode",name:"Mode"}));'
    'let o=e.modelState;if(o&&o.availableModels.length>0){'
    'let a=n&&o.availableModels.some(l=>l.modelId===n)?n:o.currentModelId;'
    'r.push(this.createSelectOption({id:"model",name:"Model",'
    'category:"model",description:"The AI model Copilot uses to generate responses.",'
    'currentValue:a,options:o.availableModels.map(l=>({value:l.modelId,'
    'name:l.name,description:l.description,_meta:l._meta}))}))}'
    'if(n){r.push({id:"reasoning_effort"})}return r}'
    'async setSessionConfigOption(e){let n=this.sessions.get(e.sessionId);'
    'let r=!0;try{switch(e.configId){case"model":{let s=e.value;'
    'if(typeof s!="string")throw new xo(-32602,"bad");let a=s.trim();'
    'if(a.length===0)throw new xo(-32602,"bad");this.validateModelAvailable(n,a),'
    'await this.applySessionModel(n,a),r=!1;break}case"allow_all":{break}}}'
    'catch(s){throw s}return{configOptions:r?await this.sendConfigOptionsUpdate('
    'e.sessionId,n):await this.buildCurrentConfigOptions(n)}}'
)


class LocalModelPatchTests(unittest.TestCase):
    def test_rewrites_model_option_only_when_label_is_set(self):
        patch = load_patch()
        result, stats = patch.build_patch(SOURCE)
        self.assertIn("COPILOT_PATCH_LOCAL_MODEL_LABEL", result)
        self.assertIn('value:"local",name:__localLabel', result)
        self.assertEqual(stats["model-options"], 1)

    def test_fixed_local_selection_is_a_noop(self):
        patch = load_patch()
        result, stats = patch.build_patch(SOURCE)
        self.assertIn('if(__localLabel){if(e.value!=="local")', result)
        self.assertEqual(stats["model-set"], 1)

    def test_patch_is_idempotent(self):
        patch = load_patch()
        result, _ = patch.build_patch(SOURCE)
        with self.assertRaises(patch.AlreadyPatched):
            patch.build_patch(result)


if __name__ == "__main__":
    unittest.main()
