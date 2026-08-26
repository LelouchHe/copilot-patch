#!/usr/bin/env python3
"""Show one fixed ACP model while Copilot BYOK routes to a local provider.

Copilot CLI pins inference to COPILOT_MODEL in BYOK mode, but its ACP config
option still advertises the GitHub cloud catalog. When
COPILOT_PATCH_LOCAL_MODEL_LABEL is set, this patch presents one logical model
with value `local` and the configured display label. Selecting that same value
is a no-op; other model values are rejected. Wire routing remains untouched.
"""

import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, NoReturn, Tuple

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        os.pardir,
        "lib",
    ),
)
from find_app_js import find_app_js  # noqa: E402

NAME = "acp-local-model"
MARKER = "COPILOT_PATCH_LOCAL_MODEL_LABEL"


class AlreadyPatched(Exception):
    pass


def fail(msg: str, code: int) -> NoReturn:
    print(f"{NAME}: ERROR {msg}", file=sys.stderr)
    sys.exit(code)


def build_patch(src: str) -> Tuple[str, Dict[str, int]]:
    if MARKER in src:
        raise AlreadyPatched()

    method = re.search(
        r"async buildConfigOptions\((\w+),(\w+)\)\{",
        src,
    )
    if not method:
        raise LookupError("buildConfigOptions anchor not found")
    selected = method.group(2)
    description = 'description:"The AI model Copilot uses to generate responses."'
    description_pos = src.find(description, method.end())
    if description_pos < 0:
        raise LookupError("ACP model option description not found")
    block_start = src.rfind(";let ", method.end(), description_pos)
    block_end_token = f"}}if({selected}){{"
    block_end = src.find(block_end_token, description_pos)
    if block_start < 0 or block_end < 0:
        raise LookupError("ACP model option block not found")

    original = src[block_start + 1 : block_end + 1]
    replacement = (
        ";let __localLabel=process.env."
        f"{MARKER}?.trim();"
        "if(__localLabel){"
        'r.push(this.createSelectOption({id:"model",name:"Model",'
        'category:"model",description:"The fixed local model used by this ACP session.",'
        'currentValue:"local",options:[{value:"local",name:__localLabel,'
        "description:__localLabel}]}))"
        "}else{"
        f"{original}"
        "}"
    )
    src = src[:block_start] + replacement + src[block_end + 1 :]

    setter = re.search(
        r'async setSessionConfigOption\((\w+)\)\{.*?case"model":\{'
        r'let \w+=\1\.value;if\(typeof \w+!="string"\)throw new (\w+)'
        r'\(-32602,',
        src,
        re.S,
    )
    if not setter:
        raise LookupError("setSessionConfigOption model anchor not found")
    request = setter.group(1)
    error_type = setter.group(2)
    insert_at = src.find('case"model":{', setter.start()) + len('case"model":{')
    guard = (
        f"let __localLabel=process.env.{MARKER}?.trim();"
        "if(__localLabel){"
        f'if({request}.value!=="local")throw new {error_type}'
        '(-32602,"This ACP session is fixed to the local model.");'
        "break}"
    )
    src = src[:insert_at] + guard + src[insert_at:]

    return src, {"model-options": 1, "model-set": 1}


def publish(path: str, src: str) -> str:
    backup = path + ".orig"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)

    tmp = path + ".patching.mjs"
    with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(src)

    node = shutil.which("node")
    if node:
        proc = subprocess.run(
            [node, "--check", tmp],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            os.unlink(tmp)
            match = re.search(
                r"^\s*(\w*(?:Syntax|Reference|Type)Error:.*)$",
                proc.stderr,
                re.M,
            )
            detail = match.group(1)[:160] if match else "node --check failed"
            fail(
                f"patched bundle is not valid JS, leaving {path} untouched. {detail}",
                3,
            )
        verdict = "verified"
    else:
        print(
            f"{NAME}: warning: node not found, skipping syntax check",
            file=sys.stderr,
        )
        verdict = "UNVERIFIED"

    os.replace(tmp, path)
    return verdict


def main(argv: List[str]) -> int:
    path = argv[1] if len(argv) > 1 else find_app_js()
    if not path or not os.path.isfile(path):
        fail(f"app.js not found: {path or '<unresolved>'}", 1)

    with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
        src = f.read()

    try:
        patched, stats = build_patch(src)
    except AlreadyPatched:
        print(f"{NAME}: already patched: {path}")
        return 0
    except LookupError as exc:
        fail(
            f"{exc}; leaving {path} untouched (upstream may have restructured)",
            2,
        )

    verdict = publish(path, patched)
    detail = ", ".join(f"{key}: {value}" for key, value in stats.items())
    print(f"{NAME}: patched {path} [{verdict}] ({detail})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
