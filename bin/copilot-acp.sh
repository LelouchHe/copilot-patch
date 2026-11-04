#!/bin/bash
# Keep the existing ACP host entry point; non-ACP arguments work too.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1

case ",${COPILOT_CLI_ENABLED_FEATURE_FLAGS:-}," in
  *,IMAGE_GENERATION_TOOL,*) ;;
  *)
    COPILOT_CLI_ENABLED_FEATURE_FLAGS="${COPILOT_CLI_ENABLED_FEATURE_FLAGS:+${COPILOT_CLI_ENABLED_FEATURE_FLAGS},}IMAGE_GENERATION_TOOL"
    ;;
esac
export COPILOT_CLI_ENABLED_FEATURE_FLAGS

python3 "$REPO_DIR/lib/apply_patches.py" >&2 ||
  echo "copilot-acp: warn: patch pipeline failed; continuing with live bundle" >&2

COPILOT_BIN="$(command -v copilot 2>/dev/null || echo /opt/homebrew/bin/copilot)"
exec "$COPILOT_BIN" "$@"
