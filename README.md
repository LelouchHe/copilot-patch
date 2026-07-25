# copilot-patch

Local patches for [GitHub Copilot CLI](https://github.com/github/copilot-cli), plus a
launcher wrapper that keeps them applied across the CLI's self-updates.

Everything here works around upstream bugs. Each patch is meant to be **deleted**
once the corresponding issue is fixed.

## Why this exists

`copilot` ships as a Node SEA binary: the Mach-O executable is just a Node runtime
plus a bootstrap loader (`__NODE_SEA_BLOB`). The actual application is a plain,
minified `app.js` that the launcher extracts to and imports from:

```
~/Library/Caches/copilot/pkg/<platform>/<version>/app.js
```

There is no integrity check on that file, so it can be patched in place. But it
lives in a **version-scoped directory**, and the CLI auto-updates itself every few
days — each update lands in a fresh directory with a pristine `app.js`, silently
dropping any patch.

Hence `bin/copilot-acp.sh`: spawn `copilot` through it and every patch is
re-applied first, on every single spawn.

## Layout

| Path | Purpose |
|---|---|
| `bin/copilot-acp.sh` | Wrapper: applies all patches, then `exec`s `copilot` with the args passed through |
| `patches/*.sh` | Individual patches; idempotent, independently runnable |
| `lib/find-app-js.py` | Resolves which `app.js` copilot will actually load |

## Finding the right app.js

Non-obvious, and getting it wrong fails silently. The loader does **not** just use
the newest directory under one cache root — it scans several roots
(`$COPILOT_CACHE_HOME`, `~/Library/Caches/copilot`, `$XDG_CACHE_HOME/copilot`,
`$COPILOT_HOME`, `~/.copilot`), each with `universal/` and `<platform>-<arch>/`
subdirectories, then picks the **highest version across all of them**.

That matters in practice: this machine had 1.0.70–1.0.74 left in `~/.copilot/pkg`
and 1.0.75 in `~/Library/Caches/copilot/pkg`. A newer build landing in the older
root would win — and a patcher that only looked at the obvious root would happily
patch a copy nobody loads.

`lib/find-app-js.py` mirrors the loader's own resolution (ported from its
`index.js`), including prerelease ordering. It does not model `--prefer-version`
or the auto-update-disabled path, which bypass the cache scan entirely; pass an
explicit path to a patch if you need those.

## Install

Point your ACP host at the wrapper instead of `copilot` directly. For
[webagent](https://github.com/LelouchHe/webagent), in `config.toml`:

```toml
agent_cmd = "/Users/<you>/mine/code/copilot-patch/bin/copilot-acp.sh --acp --context long_context"
```

Flags are passed straight through, so the CLI stays configurable from there.

Patches can also be run standalone:

```bash
./patches/acp-context-tier.sh              # resolves the loaded app.js itself
./patches/acp-context-tier.sh /path/app.js # or target one explicitly
```

## Patches

### `acp-context-tier.sh`

**Problem:** in ACP mode (`copilot --acp`), the context window tier is silently
ignored. Both `--context long_context` and `"contextTier"` in
`~/.copilot/settings.json` are dropped, and every ACP session runs on the default
context window.

**Root cause** (verified against 1.0.75):

1. The `--acp` branch in `main()` returns *before* the line that resolves
   `t.context ?? settings.contextTier`, so neither source is ever read.
2. The ACP agent module contains **zero** references to `contextTier`. It resolves
   `reasoningEffort` (CLI flag → `settings.effortLevel`) but has no equivalent for
   the tier, and never passes the field when creating or loading sessions.
3. `applySessionModel()` calls `switchTo({modelId})` only, so an ACP client
   switching models would drop the tier even if it had been set.
4. The interactive TUI has compensating logic (read settings → `options.update`)
   that ACP lacks entirely — which is why this only breaks under ACP.

**Fix:** injects `__acpContextTier()` (CLI `--context`, else `settings.contextTier`)
and wires it into four places: session create, session load, a forced
`options.update` once the session handle exists, and model switch.

The forced update is load-bearing. Passing `contextTier` at creation is not enough
on the **resume** path — a persisted session restores its own model-selection state
and overrides it. Hosts that reconnect via `session/load` (webagent does) hit this
on every restore.

**Verified** by A/B on `gpt-5.6-sol`, for both new and resumed sessions:

| | `max_prompt_tokens` | `max_context_window_tokens` |
|---|---|---|
| unpatched | 272,000 | 400,000 |
| patched | **922,000** | **1,050,000** |

**Upstream:** related but not ACP-specific — github/copilot-cli#3481, #3762.
Delete this patch once ACP honours the tier.

## Notes

- Each patch backs up the original as `app.js.orig` on first run (never
  overwritten, so it always holds a pristine copy). Roll back with
  `cp app.js.orig app.js`.
- Patches anchor on **call sites** rather than function bodies, and reverse-derive
  minified identifiers from surrounding code instead of hardcoding them. If an
  anchor stops matching, the script exits non-zero **without writing** rather than
  producing a corrupted bundle.
- The wrapper never fails the launch: a broken patch degrades to a stderr warning
  and an unpatched — but working — agent.
