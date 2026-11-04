# copilot-patch

Expose Copilot CLI's built-in `image_generation` tool in CLI and ACP sessions.
Verified against **Copilot CLI 1.0.105**, including an actual image-generation
call over ACP that returned a PNG image content block.

The CLI already ships the tool, but this installation does not expose it by
default. Two changes are needed:

1. The launcher appends `IMAGE_GENERATION_TOOL` to
   `COPILOT_CLI_ENABLED_FEATURE_FLAGS`, preserving existing flags.
2. `patches/acp-image-generation.py` adds
   `imageGeneration: {enabled: settings?.imageGeneration !== false}` to ACP's
   native boot options. The flag alone works for non-ACP sessions, but does
   **not** expose the tool over ACP.

The patch edits the cached `cli-main.js`, not native binaries, user settings,
or shell startup files. The current session's tool list does not change;
start a new session through the wrapper.

## Usage

The existing host entry point is preserved:

```bash
./bin/copilot-acp.sh --acp --context long_context
./bin/copilot-acp.sh -p 'Generate an image of the solar system'
```

The ACP bundle patch can also be applied independently:

```bash
python3 patches/acp-image-generation.py /path/to/cli-main.js
COPILOT_CLI_ENABLED_FEATURE_FLAGS=IMAGE_GENERATION_TOOL copilot --acp
```

For an ACP host, set its agent command to:

```toml
agent_cmd = "/Users/<you>/mine/code/copilot-patch/bin/copilot-acp.sh --acp --context long_context"
```

Arguments, working directory, and exit status pass through unchanged. Python 3
and Node are required to apply and validate the patch. Copilot is resolved from
`PATH`, with the existing `/opt/homebrew/bin/copilot` fallback. No host service
configuration is changed automatically.

## Patch safety and rollback

The existing pipeline rebuilds from `cli-main.js.orig` on every launch,
applies executable files in `patches/` to isolated candidates, syntax-checks
them with `node --check`, and atomically publishes the last-good stage. This
also reapplies the patch after CLI updates. The resolver selects the newest
cached package first, then `cli-main.js` (or `app.js` for older releases).

The patch requires one recognized ACP boot-options anchor and refuses
unrecognized existing image options. Anchor or validation failures are logged,
not silently treated as success. The pipeline can continue with an unpatched
bundle; the tool may then remain unavailable over ACP.

To disable image generation, use Copilot's `imageGeneration: false` setting.
To remove the bundle patch, make `patches/acp-image-generation.py`
non-executable and run `python3 lib/apply_patches.py`; it rebuilds from the
pristine backup without that patch. Then launch plain `copilot` without the
wrapper or feature-flag override. Do not copy a backup between CLI versions.

## Limits

This enables a local feature flag, not account access. Authentication, model
policies, tool permissions, and image-generation billing remain Copilot's
responsibility. Image generation consumes AI credits separately from chat.
The launcher does not change `imageGeneration: false`, disabled feature flags,
or available/excluded tool filters. Copilot still decides their precedence.

ACP can carry image content, but a client must implement image rendering.
Tool availability and successfully displaying an image are separate checks.
The flag is an internal CLI interface and may change in a later release.
The resolver follows the normal cached-package launch path. Do not combine
this wrapper with `--no-auto-update`, a version-pinned launch, or
`COPILOT_AUTO_UPDATE=false`: those can select a different embedded bundle.

## Archives and tests

The retired long-context and BYOK patches are in `archives/`, along with the
[original documentation](archives/README.md). Their upstream bugs no longer
reproduce in 1.0.105. They are not run by the launcher.

`lib/` retains the staged patch pipeline, with its resolver updated for
`cli-main.js`. Archived patch tests still run; archived executables are outside
`patches/` and are never applied by the launcher.

Run the launcher and archived-patch regression tests:

```bash
python3 -m unittest discover -s tests
python3 tests/test_find_app_js.py
bash -n bin/copilot-acp.sh
```

Launcher tests use a stand-in Copilot process and do not call image services.
ACP option tests execute the transformed JavaScript in Node, including the
explicit settings opt-out and unchanged context/tool filters.
