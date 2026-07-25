#!/bin/bash
# Launcher wrapper for `copilot --acp`, used as webagent's agent_cmd.
#
# Why a wrapper: copilot self-updates into a versioned directory
# (~/Library/Caches/copilot/pkg/<platform>/<version>/app.js), so any local patch
# is lost on every update. Patching from the host's service start script only
# covers a full service restart -- webagent's bridge.restart() respawns the agent
# directly and would silently pick up an unpatched build.
#
# Spawning copilot through this wrapper covers every spawn path, and keeps the
# host generic: webagent is an ACP host for several agents, so copilot-specific
# workarounds belong here rather than in its source.
#
# Args are passed straight through, e.g. in config.toml:
#   agent_cmd = "/Users/<you>/mine/code/copilot-patch/bin/copilot-acp.sh --acp --context long_context"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Apply every patch in patches/. All are idempotent and best-effort: a failure
# must never stop the agent from starting. Any executable is accepted, so a patch
# can be written in whatever suits it. Output goes to stderr so it lands in the
# host's log instead of corrupting the ACP stdio channel.
for patch in "$REPO_DIR"/patches/*; do
  [ -f "$patch" ] && [ -x "$patch" ] || continue
  "$patch" >&2 || echo "copilot-acp: warn: $(basename "$patch") failed; continuing" >&2
done

# Resolve copilot from PATH, with a Homebrew fallback in case this is spawned
# from an environment that never sourced the user's shell env.
COPILOT_BIN="$(command -v copilot 2>/dev/null || echo /opt/homebrew/bin/copilot)"

exec "$COPILOT_BIN" "$@"
