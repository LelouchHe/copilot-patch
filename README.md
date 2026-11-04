# copilot-patch

Local patches for [GitHub Copilot CLI](https://github.com/github/copilot-cli), plus a
launcher wrapper that keeps them applied across the CLI's self-updates.

Everything here works around upstream bugs. Each patch is meant to be **deleted**
once the corresponding issue is fixed.

## Why this exists

`copilot` ships as a Node SEA binary: the Mach-O executable is just a Node runtime
plus a bootstrap loader (`__NODE_SEA_BLOB`). The actual application is a plain,
minified JavaScript bundle that the launcher extracts to and imports from a
version-scoped package cache, for example:

```
~/.copilot/pkg/<platform>/<version>/cli-main.js
```

In CLI 1.0.105, `app.js` is a bootstrap and the application lives in
`cli-main.js`. Older releases kept the application in `app.js`.

There is no integrity check on that file, so it can be patched in place. But it
lives in a **version-scoped directory**, and the CLI auto-updates itself every few
days — each update lands in a fresh directory with a pristine bundle, silently
dropping any patch.

Hence `bin/copilot-acp.sh`: spawn `copilot` through it and every patch is
re-applied first, on every single spawn.

## Layout

| Path | Purpose |
|---|---|
| `bin/copilot-acp.sh` | Wrapper: enables the image-tool flag, runs the staged patch pipeline, then `exec`s `copilot` with the args passed through |
| `lib/apply_patches.py` | Rebuilds from pristine source, isolates each patch, and atomically publishes the last-good stage |
| `patches/*` | Individual patches; any executable, idempotent, independently runnable |
| `lib/find_app_js.py` | Resolves which `cli-main.js` (or older `app.js`) copilot will actually load |
| `archives/` | Retired patches and the reasons they are no longer needed |
| `tests/` | Tests for the resolver, patch pipeline, launcher, and structured patch logic |

## Why bash and Python

The wrapper is bash because its whole job is *process plumbing*: run the Python
pipeline, then `exec` copilot so the agent replaces this process rather than
hanging a supervisor off it. That keeps the process tree flat, signals direct,
and the host's `ps` output honest.

Patches are Python because their job is the opposite: careful text surgery on a
multi-megabyte minified bundle, with regex, atomic replace, and subprocess
verification.

Node/TypeScript would work — Node 24's `process.execve` gives real process
replacement, so even the wrapper is expressible. It is not used because it buys
nothing here and costs robustness: a TS wrapper would need node on `PATH` plus
type-stripping support just to launch. Python and node remain soft dependencies
for patching: if the pipeline cannot run or node cannot validate JavaScript, the
wrapper leaves the live bundle untouched and still starts Copilot.

The one thing deliberately avoided is mixing the two. An earlier version was a
bash script that was 75% Python heredoc — the worst of both, with no syntax
highlighting, no linting, and quoting hazards. The boundary is now crisp: bash for
process control, Python for logic.

### On the missing type system

A fair objection, and the honest answer is that it buys little *here*. Of the five
bugs hit while building this, a static type checker would have caught **none**:
the helper injection that produced invalid JS, the resolver scanning one cache
root too few, the tier being overridden on the resume path, a fragile `sort -k`
field number, and an error message grabbing the wrong line. Every one is
well-typed — `str` in, `str` out — and wrong anyway.

That is inherent to the domain. The input is megabytes of minified JavaScript
and the output is the same string, edited. Nothing interesting is expressible
as a type; what actually matters is whether a regex landed in the right place,
whether the result still parses, and whether the patched program behaves
correctly at runtime.

So the verification lives where the risk is, not in the patcher's signatures:

| Risk | Gate |
|---|---|
| Regex matched the wrong place | `node --check` before publishing |
| Upstream restructured | Anchor miss → exit 2, nothing written |
| Resolver picks a copy nobody loads | `tests/`, mutation-checked |
| Patch does not actually work | A/B probe against a live agent |

Type hints are present anyway — they cost nothing at runtime, document intent, and
make `mypy`/`pyright` usable if the code ever grows structured data worth checking.

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

`lib/find_app_js.py` mirrors the loader's own resolution (ported from its
`index.js`), including prerelease ordering. It keeps its historical name, but
now selects `cli-main.js` when present in the winning package, falling back to
`app.js` for older releases.

It does not model `--prefer-version` or the auto-update-disabled path, which
bypass the cache scan entirely; pass an explicit path to a patch if you need
those. Do not combine the wrapper with `--no-auto-update`, a version-pinned
launch, or `COPILOT_AUTO_UPDATE=false`: those can select a different embedded
bundle.

## Install

Point your ACP host at the wrapper instead of `copilot` directly. For
[webagent](https://github.com/LelouchHe/webagent), in `config.toml`:

```toml
agent_cmd = "/Users/<you>/mine/code/copilot-patch/bin/copilot-acp.sh --acp --context long_context"
```

Flags are passed straight through, so the CLI stays configurable from there.
Arguments, working directory, and exit status are preserved. Copilot is resolved
from `PATH`, with a Homebrew fallback at `/opt/homebrew/bin/copilot`. No host
service configuration is changed automatically.

Patches can also be run standalone, though normal launches should use the
pipeline:

```bash
./patches/acp-image-generation.py                     # resolves the loaded bundle
./patches/acp-image-generation.py /path/cli-main.js   # or target one explicitly
COPILOT_CLI_ENABLED_FEATURE_FLAGS=IMAGE_GENERATION_TOOL copilot --acp
```

The wrapper also accepts non-ACP prompts:

```bash
./bin/copilot-acp.sh -p 'Generate an image of the solar system'
```

Start a new session through the wrapper; already-running sessions do not gain
new tools.

## Patch pipeline

Every wrapper launch rebuilds the loaded bundle from `cli-main.js.orig` (or
`app.js.orig` on older releases) and applies executable files in `patches/` in
filename order. Each patch receives its own candidate copy:

1. Success plus JavaScript validation promotes the candidate to last-good.
2. Failure discards only that candidate and continues with the next patch.
3. A final `node --check` gates one atomic publish of the last-good stage.
4. A fatal pipeline failure leaves the live bundle untouched and never blocks
   Copilot startup.

The wrapper logs the complete result:

```text
copilot-patch:
  APPLIED  acp-image-generation.py
  RESULT   1 applied, 0 failed
```

Patches should be independent. Filename order is deterministic for the few cases
where one patch intentionally builds on another. Removing or making a patch
non-executable removes it from the next rebuild; no reverse patch is needed.

## Patches

### `acp-image-generation.py`

**Problem:** Copilot CLI ships the `image_generation` tool, but it is not exposed
by default on this installation. Enabling its feature flag works in non-ACP
sessions, while ACP sessions still do not advertise the tool.

**Root cause** (verified against 1.0.105):

1. Tool availability is gated by `IMAGE_GENERATION_TOOL`.
2. ACP's native session factory also needs an explicit `imageGeneration.enabled`
   option, which the JavaScript boot-options builder does not pass.

**Fix:** the wrapper appends `IMAGE_GENERATION_TOOL` to
`COPILOT_CLI_ENABLED_FEATURE_FLAGS`, preserving existing flags. The patch adds
`imageGeneration: {enabled: settings?.imageGeneration !== false}` to ACP's boot
options, deriving the minified settings identifier from the surrounding code.

Both changes are required for ACP. There is no image-generation command-line
option in 1.0.105; adding a startup argument alone is not sufficient. The
`imageGeneration: false` setting and existing tool filters remain respected.

**Verified** by A/B on Copilot CLI 1.0.105:

| | Non-ACP | ACP |
|---|---|---|
| unpatched, no feature flag | Tool unavailable | Tool unavailable |
| feature flag only | Tool available | Tool unavailable |
| feature flag and ACP patch | Tool available | Tool available |

An actual ACP tool call generated a 1536×1024 PNG and returned an image content
block with Base64 data. The decoded bytes matched the saved image. Clients
still need to render image content; receiving an image and displaying it are
separate concerns.

This enables local tool wiring, not account access. Authentication, model
policies, tool permissions, and image-generation billing remain Copilot's
responsibility. Image generation consumes AI credits separately from chat.
The internal feature flag may change in a future release.

Delete this patch once upstream exposes the tool over ACP with the supported
configuration.

The retired long-context and BYOK patches are in [archives/](archives/README.md),
with their purposes and retirement reasons. They are outside `patches/` and
are never applied by the launcher.

## Safety

Patching a minified bundle in place is inherently sharp, so failures are made
loud and non-destructive:

- **Verify before publishing.** A patch writes to a temp file, runs
  `node --check` on it, and only then renames it over the target. A regex that
  matches in the wrong place produces invalid JS, and copilot would then fail to
  start at all — much worse than an unpatched agent. This is not hypothetical: it
  happened while developing `acp-context-tier.py`, where an injection swallowed
  the following method name.
- **Anchor misses abort.** Patches key off call sites and reverse-derive minified
  identifiers from surrounding code rather than hardcoding them. If an anchor
  stops matching, the script exits non-zero **without writing**. The image patch
  also refuses unrecognized existing image options.
- **The wrapper never blocks startup.** Any patch failure degrades to a stderr
  warning and an unpatched — but working — agent.
- **Originals are kept.** First run saves `cli-main.js.orig` (or `app.js.orig` on
  older releases) and never overwrites it. Every launch rebuilds from that
  pristine copy, so removing a patch automatically rolls it back while retaining
  all other successful patches. Do not copy backups between CLI versions.
- **Patch failures are isolated.** A failed candidate is discarded and later
  independent patches continue from the previous last-good stage.
- **Publication is atomic.** The live bundle changes only once, after the final
  staged bundle passes syntax validation.
- **Pristine backup creation is atomic.** An interrupted first copy cannot leave
  a partial `.orig` that future rebuilds mistake for the source of truth.
- **Validation is mandatory for publication.** If node is unavailable, the
  pipeline preserves the live bundle rather than publishing unchecked code.

To disable image generation, use Copilot's `imageGeneration: false` setting.
To remove the bundle patch, make `patches/acp-image-generation.py`
non-executable and run `python3 lib/apply_patches.py`. Then launch plain
`copilot` without the wrapper or feature-flag override.

## Testing

```bash
python3 -m unittest discover -s tests
python3 tests/test_find_app_js.py
bash -n bin/copilot-acp.sh
```

The pipeline suite verifies skip-on-failure, continuation, rebuild-from-pristine,
and preservation of the live bundle when final validation fails. Patch-specific
tests cover structured transformations where practical. Every patch and the
final pipeline output are also checked against JavaScript syntax at runtime.

Launcher tests use a stand-in Copilot process and do not call image services.
ACP option tests execute the transformed JavaScript in Node, including the
explicit settings opt-out and unchanged context/tool filters. Tests for the
archived patches are retained.
