#!/usr/bin/env python3
"""Tests for lib/find_app_js.py.

Only the resolver is tested here, deliberately. It is pure logic, it already
shipped one real bug (scanning a single cache root, and so picking a copy the
loader would not load), and its failure mode is silent -- patching the wrong
app.js still reports success.

The patch scripts themselves are not unit tested: their regex anchors are only
meaningful against a real copilot bundle, and they already self-verify at
runtime by refusing to write a bundle that fails `node --check`.

Run: python3 tests/test_find_app_js.py
"""

import os
import platform
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOLVER = os.path.join(REPO, "lib", "find_app_js.py")

ARCH = {"x86_64": "x64", "amd64": "x64",
        "aarch64": "arm64", "arm64": "arm64"}.get(
            platform.machine().lower(), platform.machine().lower())
PLAT = {"darwin": "darwin", "windows": "win32"}.get(
    platform.system().lower(), platform.system().lower())
PLATFORM_DIR = f"{PLAT}-{ARCH}"

failures = []


def make_app(root, subdir, version):
    d = os.path.join(root, "pkg", subdir, version)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "app.js"), "w") as f:
        f.write("// stub\n")
    return os.path.join(d, "app.js")


def resolve(home, cache_home=None):
    """Run the resolver in isolation, with only the temp roots visible."""
    env = dict(os.environ)
    env["HOME"] = home
    env["XDG_CACHE_HOME"] = os.path.join(home, ".cache")
    env.pop("COPILOT_HOME", None)
    if cache_home:
        env["COPILOT_CACHE_HOME"] = cache_home
    else:
        env.pop("COPILOT_CACHE_HOME", None)
    proc = subprocess.run([sys.executable, RESOLVER],
                          capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout.strip()


def check(name, got, want):
    if got == want:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}\n          got:  {got}\n          want: {want}")
        failures.append(name)


def test_highest_version_within_one_root():
    with tempfile.TemporaryDirectory() as home:
        cache = os.path.join(home, "Library", "Caches", "copilot")
        make_app(cache, PLATFORM_DIR, "1.0.9")
        make_app(cache, PLATFORM_DIR, "1.0.70")
        want = make_app(cache, PLATFORM_DIR, "1.0.75")
        # 1.0.9 must lose to 1.0.70: numeric compare, not lexicographic.
        check("highest version within one root", resolve(home)[1], want)


def test_highest_version_across_roots():
    """The regression that motivated this file."""
    with tempfile.TemporaryDirectory() as home:
        cache = os.path.join(home, "Library", "Caches", "copilot")
        make_app(cache, PLATFORM_DIR, "1.0.75")
        # Secondary root holds the newer build; the loader would load this one.
        want = make_app(os.path.join(home, ".copilot"), PLATFORM_DIR, "1.0.99")
        check("highest version wins across cache roots", resolve(home)[1], want)


def test_prerelease_sorts_after_release():
    with tempfile.TemporaryDirectory() as home:
        cache = os.path.join(home, "Library", "Caches", "copilot")
        make_app(cache, PLATFORM_DIR, "1.0.99-0")
        want = make_app(cache, PLATFORM_DIR, "1.0.99")
        check("release beats its prerelease", resolve(home)[1], want)


def test_universal_dir_considered():
    with tempfile.TemporaryDirectory() as home:
        cache = os.path.join(home, "Library", "Caches", "copilot")
        make_app(cache, PLATFORM_DIR, "1.0.75")
        want = make_app(cache, "universal", "1.0.80")
        check("universal/ is scanned too", resolve(home)[1], want)


def test_cache_home_env_root():
    with tempfile.TemporaryDirectory() as home:
        with tempfile.TemporaryDirectory() as alt:
            cache = os.path.join(home, "Library", "Caches", "copilot")
            make_app(cache, PLATFORM_DIR, "1.0.75")
            want = make_app(alt, PLATFORM_DIR, "1.0.90")
            check("COPILOT_CACHE_HOME root is scanned",
                  resolve(home, cache_home=alt)[1], want)


def test_no_candidates_exits_nonzero():
    with tempfile.TemporaryDirectory() as home:
        code, out = resolve(home)
        check("no app.js anywhere -> exit 1 and no path",
              (code != 0, out), (True, ""))


if __name__ == "__main__":
    print(f"find-app-js resolver ({PLATFORM_DIR})")
    for fn in [
        test_highest_version_within_one_root,
        test_highest_version_across_roots,
        test_prerelease_sorts_after_release,
        test_universal_dir_considered,
        test_cache_home_env_root,
        test_no_candidates_exits_nonzero,
    ]:
        fn()

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        sys.exit(1)
    print("all passed")
