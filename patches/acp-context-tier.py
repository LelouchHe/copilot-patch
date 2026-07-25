#!/usr/bin/env python3
"""Make `--context` / `settings.contextTier` work in copilot's ACP mode.

Why: copilot's ACP server (`copilot --acp`) never passes `contextTier` when
creating sessions. It resolves `reasoningEffort` (CLI flag -> settings.effortLevel)
but has no equivalent for the context tier, so both `--context long_context` and
`"contextTier"` in settings.json are silently dropped and every ACP session runs
on the default context window.

This patches the bundled app.js to:
  1. inject __acpContextTier()  -- CLI --context, else settings.contextTier
  2. inject __acpApplyTier()    -- force the tier onto a live session
  3. pass contextTier when creating / loading ACP sessions
  4. force-apply it once the session handle exists
  5. preserve the tier when the ACP client switches model

Step 4 is load-bearing. Passing contextTier at creation is not enough on the
resume path: a persisted session restores its own model-selection state and
overrides it. Hosts that reconnect via session/load hit this on every restore.

Idempotent. Safe to re-run. Must be re-run after every copilot update, since the
updater downloads a fresh app.js -- ../bin/copilot-acp.sh does that automatically
on every spawn.

Verified on copilot 1.0.75. Anchors are call sites rather than function bodies,
so an anchor miss is a clean error (exit 2, file untouched). The result is also
syntax-checked before it replaces the original (exit 3 if it does not parse), so
a bad splice can never leave copilot unable to start.

Usage: acp-context-tier.py [path/to/app.js]
"""

import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, NoReturn, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "lib"))
from find_app_js import find_app_js  # noqa: E402

NAME = "acp-context-tier"
MARKER = "__acpContextTier"


def fail(msg: str, code: int) -> NoReturn:
    print(f"{NAME}: ERROR {msg}", file=sys.stderr)
    sys.exit(code)


def build_patch(src: str) -> Tuple[str, Dict[str, object]]:
    """Return patched source plus a dict of what was touched.

    Raises LookupError if a required anchor is missing, so the caller can bail
    out without having written anything.
    """
    # The ACP agent resolves reasoning effort like this (minified names vary by
    # version):
    #   async resolveInitialReasoningEffort(){let e=this.resolveCliReasoningEffort();
    #   if(e!==void 0)return e;try{return(await Gt.load(this.options.settings))?.effortLevel}
    # Reuse the same settings loader identifier so we stay version-agnostic.
    anchor = re.search(
        r"async resolveInitialReasoningEffort\(\)\{.*?"
        r"\(await (\w+)\.load\(this\.options\.settings\)\)\?\.effortLevel",
        src, re.S)
    if not anchor:
        raise LookupError("resolveInitialReasoningEffort anchor not found")
    loader = anchor.group(1)

    helper = (
        "async __acpContextTier(){"
        "try{"
        "let t=this.options.options?.context;"
        'if(t!==void 0&&t!==null&&t!=="")return t;'
        f"return(await {loader}.load(this.options.settings))?.contextTier"
        "}catch{return}}"
        "async __acpApplyTier(s){"
        "try{"
        "let t=await this.__acpContextTier();"
        "if(t===void 0||t===null)return;"
        "await s.options.update({contextTier:t})"
        "}catch(e){}}"
        "async resolveInitialReasoningEffort(){"
    )
    src, n = re.subn(r"async resolveInitialReasoningEffort\(\)\{", helper,
                     src, count=1)
    if n != 1:
        raise LookupError("failed to inject helpers")

    # Pass the tier when creating / loading ACP sessions.
    src, n_create = re.subn(
        r'clientKind:"acp",',
        'clientKind:"acp",contextTier:await this.__acpContextTier(),',
        src)
    if n_create == 0:
        raise LookupError("no ACP session-create sites found")

    # Creation-time is not enough on resume (see module docstring). Force the
    # tier onto the live session; both newSession and loadSession call
    # wirePermissionHandling right after the handle is ready.
    src, n_apply = re.subn(
        r"this\.wirePermissionHandling\((\w+),(\w+)\);",
        lambda m: (f"await this.__acpApplyTier({m.group(1)}),"
                   f"this.wirePermissionHandling({m.group(1)},{m.group(2)});"),
        src)
    if n_apply == 0:
        raise LookupError("no wirePermissionHandling sites found")

    # Keep the tier when the ACP client switches model, otherwise switchTo
    # resets it. Best-effort: this only affects mid-session model changes.
    src, n_switch = re.subn(
        r"async applySessionModel\((\w+),(\w+)\)\{"
        r"await \1\.session\.model\.switchTo\(\{modelId:\2\}\)\}",
        lambda m: (
            f"async applySessionModel({m.group(1)},{m.group(2)})"
            "{let __t=await this.__acpContextTier();"
            f"await {m.group(1)}.session.model.switchTo("
            f"{{modelId:{m.group(2)},...__t!==void 0?{{contextTier:__t}}:{{}}}})}}"
        ),
        src, count=1)

    return src, {"session sites": n_create, "force-apply": n_apply,
                 "model-switch": n_switch, "loader": loader}


def publish(path: str, src: str) -> str:
    """Write src over path, but only if it is still valid JavaScript.

    A regex that matches in the wrong place yields a bundle that is subtly
    invalid, and copilot then fails to start at all -- far worse than an
    unpatched agent. This happened while developing this patch (an injection
    swallowed the following method name), so the check is not hypothetical.

    Write a sibling temp file, validate it, then rename over the target. Same
    directory keeps the rename atomic, and the .mjs suffix is required because
    the bundle is an ES module that `node --check` would otherwise parse as
    CommonJS.
    """
    backup = path + ".orig"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)

    tmp = path + ".patching.mjs"
    with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(src)

    node = shutil.which("node")
    if node:
        proc = subprocess.run([node, "--check", tmp],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            os.unlink(tmp)
            # node echoes the offending source line first, and in a minified
            # bundle that is enormous and often contains "Error" itself. Pick
            # out the actual diagnostic and cap it.
            match = re.search(
                r"^\s*(\w*(?:Syntax|Reference|Type)Error:.*)$",
                proc.stderr, re.M)
            detail = match.group(1)[:160] if match else "node --check failed"
            fail(f"patched bundle is not valid JS, leaving {path} "
                 f"untouched. {detail}", 3)
        verdict = "verified"
    else:
        # The wrapper must never block startup, so a missing node degrades to a
        # warning rather than refusing to patch.
        print(f"{NAME}: warning: node not found, skipping syntax check",
              file=sys.stderr)
        verdict = "UNVERIFIED"

    os.replace(tmp, path)
    return verdict


def main(argv: List[str]) -> int:
    # Resolve the app.js copilot will actually load. The loader scans several
    # cache roots and picks by version, so "newest under the obvious root" is
    # not good enough -- see lib/find_app_js.py.
    path = argv[1] if len(argv) > 1 else find_app_js()
    if not path or not os.path.isfile(path):
        fail(f"app.js not found: {path or '<unresolved>'}", 1)

    with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
        src = f.read()

    if MARKER in src:
        print(f"{NAME}: already patched: {path}")
        return 0

    try:
        patched, stats = build_patch(src)
    except LookupError as e:
        fail(f"{e}; leaving {path} untouched "
             f"(upstream may have restructured)", 2)

    verdict = publish(path, patched)
    detail = ", ".join(f"{k}: {v}" for k, v in stats.items())
    print(f"{NAME}: patched {path} [{verdict}] ({detail})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
