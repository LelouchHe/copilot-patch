#!/usr/bin/env python3
"""Resolve the app.js that `copilot` will actually load, and print its path.

copilot is a Node SEA: the binary is a runtime plus a bootstrap loader, and the
real application is a plain app.js under a version-scoped package cache. The
loader does not simply use the newest directory it can find -- it scans several
cache roots and picks a winner by version. Patching the wrong copy fails
silently, so this mirrors the loader's own resolution instead of guessing.

Ported from the loader's index.js (verified against 1.0.75), where the relevant
helpers are:

    K()  -> cache roots, in order, deduplicated
    Se() -> for each root, [root/universal, root/<platform>-<arch>]
    J()  -> collect dirs containing app.js; sort by basename version descending,
            ties broken by full path ascending; caller takes [0]

Caveats this deliberately does not model:

  * `--prefer-version <v>` pins an exact version.
  * With auto-update disabled (`--no-auto-update`, `--prefer-version`, or
    COPILOT_AUTO_UPDATE=false) the loader skips the cache scan entirely and uses
    the build embedded in the binary.

Both are opt-in and not how a long-running ACP host is normally launched. Pass an
explicit path to a patch script if you need to target something specific.
"""

import os
import platform
import re
import sys


def platform_arch():
    """Mirror the loader's `xe()`: <platform>-<arch>, with musl linux special-cased."""
    system = platform.system().lower()
    if system == "darwin":
        plat = "darwin"
    elif system == "windows":
        plat = "win32"
    else:
        plat = system

    arch = {
        "x86_64": "x64", "amd64": "x64",
        "aarch64": "arm64", "arm64": "arm64",
    }.get(platform.machine().lower(), platform.machine().lower())

    # The loader spells musl linux "linuxmusl". Detecting libc reliably is more
    # trouble than it is worth here, so probe for both.
    return f"{plat}-{arch}", (f"linuxmusl-{arch}" if plat == "linux" else None)


def cache_roots():
    """Mirror the loader's `K()`, preserving order and dropping duplicates."""
    home = os.path.expanduser("~")
    roots = []

    if os.environ.get("COPILOT_CACHE_HOME"):
        roots.append(os.path.join(os.environ["COPILOT_CACHE_HOME"], "pkg"))

    system = platform.system().lower()
    if system == "darwin":
        primary = os.path.join(home, "Library", "Caches", "copilot")
    elif system == "windows":
        primary = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.join(home, ".cache"), "copilot")
    else:
        primary = os.path.join(
            os.environ.get("XDG_CACHE_HOME") or os.path.join(home, ".cache"), "copilot")
    roots.append(os.path.join(primary, "pkg"))

    xdg = os.environ.get("XDG_CACHE_HOME") or os.path.join(home, ".cache")
    roots.append(os.path.join(xdg, "copilot", "pkg"))

    if os.environ.get("COPILOT_HOME"):
        roots.append(os.path.join(os.environ["COPILOT_HOME"], "pkg"))

    roots.append(os.path.join(home, ".copilot", "pkg"))

    seen, out = set(), []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def parse_version(name):
    """Mirror `j()`: leading X.Y.Z of a directory name, or None."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", name)
    return tuple(int(g) for g in m.groups()) if m else None


def sort_key(path):
    """Mirror the `Qe`-based comparator: version desc, prerelease last, path asc.

    Expressed as an ascending sort key, so version components are negated and the
    prerelease flag orders after its release counterpart.
    """
    name = os.path.basename(path)
    v = parse_version(name)
    if v is None:
        # The loader ranks unparseable names lowest.
        return (1, (0, 0, 0), 0, path)
    return (0, tuple(-c for c in v), 1 if "-" in name else 0, path)


def find_app_js():
    candidates = []
    pa, pa_musl = platform_arch()
    for root in cache_roots():
        for sub in [s for s in ("universal", pa, pa_musl) if s]:
            d = os.path.join(root, sub)
            if not os.path.isdir(d):
                continue
            try:
                entries = os.listdir(d)
            except OSError:
                continue
            for entry in entries:
                if os.access(os.path.join(d, entry, "app.js"), os.R_OK):
                    candidates.append(os.path.join(d, entry))
    if not candidates:
        return None
    candidates.sort(key=sort_key)
    return os.path.join(candidates[0], "app.js")


def main():
    resolved = find_app_js()
    if not resolved:
        print("find-app-js: no app.js found in any copilot package cache",
              file=sys.stderr)
        return 1
    print(resolved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
