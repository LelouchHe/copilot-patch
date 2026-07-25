#!/bin/bash
# Patch Copilot CLI so that --context / settings.contextTier is honored in ACP mode.
#
# Why: copilot's ACP server (`copilot --acp`) creates sessions without ever passing
# `contextTier`. It resolves `reasoningEffort` (CLI flag -> settings.effortLevel) but
# has no equivalent for the context tier, so `--context long_context` and
# settings.json `"contextTier": "long_context"` are silently dropped and every ACP
# session runs on the default context window.
#
# This patch adds the missing resolution to the bundled app.js:
#   1. injects a __acpContextTier() helper (CLI --context, else settings.contextTier)
#   2. passes contextTier when creating / loading ACP sessions
#   3. preserves the tier when the ACP client switches model
#
# Idempotent. Safe to re-run. Must be re-run after every copilot update, since the
# updater downloads a fresh app.js -- ../bin/copilot-acp.sh does that automatically
# on every spawn.
#
# Verified on copilot 1.0.75. Anchors are call sites rather than function bodies,
# so it degrades to a clean error (exit 2, file untouched) rather than corrupting
# the bundle if upstream restructures.
#
# Usage: acp-context-tier.sh [path/to/app.js]

set -euo pipefail

CACHE_ROOT="$HOME/Library/Caches/copilot/pkg"

find_app_js() {
  # newest (by version) app.js under the copilot package cache; all candidates share
  # the same path prefix, so a plain version sort on the full path is sufficient
  find "$CACHE_ROOT" -maxdepth 3 -name app.js -type f 2>/dev/null | sort -V | tail -1
}

APP_JS="${1:-$(find_app_js)}"

if [ -z "$APP_JS" ] || [ ! -f "$APP_JS" ]; then
  echo "acp-context-tier: app.js not found under $CACHE_ROOT" >&2
  exit 1
fi

APP_JS="$APP_JS" python3 <<'PY'
import os, re, shutil, sys

path = os.environ["APP_JS"]
src = open(path, "r", encoding="utf-8", errors="surrogateescape").read()

MARKER = "__acpContextTier"
if MARKER in src:
    print(f"acp-context-tier: already patched: {path}")
    sys.exit(0)

# The ACP agent resolves reasoning effort like this (minified names vary by version):
#   async resolveInitialReasoningEffort(){let e=this.resolveCliReasoningEffort();
#   if(e!==void 0)return e;try{return(await Gt.load(this.options.settings))?.effortLevel}...
# Reuse the same settings loader identifier so we stay version-agnostic.
anchor = re.search(
    r"async resolveInitialReasoningEffort\(\)\{.*?\(await (\w+)\.load\(this\.options\.settings\)\)\?\.effortLevel",
    src, re.S)
if not anchor:
    print("acp-context-tier: ERROR resolveInitialReasoningEffort anchor not found", file=sys.stderr)
    sys.exit(2)

loader = anchor.group(1)

helper = (
    "async __acpContextTier(){"
    "try{"
    "let t=this.options.options?.context;"
    "if(t!==void 0&&t!==null&&t!==\"\")return t;"
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
src, n = re.subn(r"async resolveInitialReasoningEffort\(\)\{", helper, src, count=1)
assert n == 1

# Passing contextTier at create time is not enough on the resume path: a persisted
# session restores its own model-selection state and overrides the option. Force the
# tier onto the live session once it exists (both newSession and loadSession run
# wirePermissionHandling right after the session handle is ready).
src, n_apply = re.subn(
    r"this\.wirePermissionHandling\((\w+),(\w+)\);",
    lambda m: (f"await this.__acpApplyTier({m.group(1)}),"
               f"this.wirePermissionHandling({m.group(1)},{m.group(2)});"),
    src)
if n_apply == 0:
    print("acp-context-tier: ERROR no wirePermissionHandling sites found", file=sys.stderr)
    sys.exit(2)

# Pass the tier when creating / loading ACP sessions.
src, n_create = re.subn(
    r'clientKind:"acp",',
    'clientKind:"acp",contextTier:await this.__acpContextTier(),',
    src)
if n_create == 0:
    print("acp-context-tier: ERROR no ACP session-create sites found", file=sys.stderr)
    sys.exit(2)

# Keep the tier when the ACP client switches model, otherwise switchTo resets it.
src, n_switch = re.subn(
    r"async applySessionModel\((\w+),(\w+)\)\{await \1\.session\.model\.switchTo\(\{modelId:\2\}\)\}",
    lambda m: (
        f"async applySessionModel({m.group(1)},{m.group(2)})"
        "{let __t=await this.__acpContextTier();"
        f"await {m.group(1)}.session.model.switchTo("
        f"{{modelId:{m.group(2)},...__t!==void 0?{{contextTier:__t}}:{{}}}})}}"
    ),
    src, count=1)

backup = path + ".orig"
if not os.path.exists(backup):
    shutil.copy2(path, backup)

with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
    f.write(src)

print(f"acp-context-tier: patched {path} "
      f"(session sites: {n_create}, force-apply: {n_apply}, "
      f"model-switch: {n_switch}, loader: {loader})")
PY
