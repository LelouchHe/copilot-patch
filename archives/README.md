# Archived patches

These patches are retained for history and are not applied by the launcher.
Both were retired after evaluation against **Copilot CLI 1.0.105**.

## `acp-context-tier.py`

**Purpose:** make ACP honour `--context` and `settings.contextTier`, including
resumed sessions and model switches, instead of silently using the default tier.

**Why retired:** upstream now forwards the context tier at ACP startup, preserves
the explicit CLI value on resume, and carries the tier through model switches.
Use `copilot --acp --context long_context` directly; this workaround is no longer
needed. Changing the context tier through ACP session configuration remains a
separate upstream feature gap, not something this patch implemented.

## `acp-local-model.py`

**Purpose:** under BYOK, replace ACP's misleading selectable cloud-model catalog
with one labelled local model, without changing the provider or inference route.

**Why retired:** the original bug no longer reproduces. In 1.0.105, ACP under
BYOK advertises neither a model selector nor a cloud-model catalog. Upstream
does not provide this patch's labelled local selector, but it no longer offers
cloud choices that misrepresent the actual route.

Both scripts also target an older `app.js` structure. In 1.0.105 the application
moved to `cli-main.js`, their anchors no longer match, and they must not be put
back into the active `patches/` directory.
