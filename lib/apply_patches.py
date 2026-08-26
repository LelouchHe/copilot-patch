#!/usr/bin/env python3
"""Apply Copilot bundle patches as an isolated, staged pipeline."""

from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Optional, Tuple

from find_app_js import find_app_js


Validator = Callable[[str], Tuple[bool, str]]


@dataclass
class PipelineResult:
    applied: list[str]
    failed: list[str]
    published: bool


def validate_javascript(path: str) -> Tuple[bool, str]:
    node = shutil.which("node")
    if not node:
        return False, "node is required to validate the staged bundle"
    proc = subprocess.run(
        [node, "--check", path],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return True, "verified"
    lines = [line.strip() for line in proc.stderr.splitlines() if line.strip()]
    detail = lines[-1] if lines else "node --check failed"
    return False, detail[:240]


def patch_files(patches_dir: str) -> list[Path]:
    root = Path(patches_dir)
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.iterdir()
        if path.is_file() and os.access(path, os.X_OK) and not path.name.startswith(".")
    )


def last_detail(proc: subprocess.CompletedProcess[str]) -> str:
    lines = [
        line.strip()
        for text in (proc.stderr, proc.stdout)
        for line in text.splitlines()
        if line.strip()
    ]
    return (lines[-1] if lines else f"exit {proc.returncode}")[:240]


def cleanup_candidate(path: str) -> None:
    for extra in (path, path + ".orig", path + ".patching.mjs"):
        try:
            os.unlink(extra)
        except FileNotFoundError:
            pass


def ensure_original(app: Path, original: Path) -> None:
    if original.exists():
        return
    fd, temporary = tempfile.mkstemp(
        prefix=original.name + ".creating.",
        dir=original.parent,
    )
    os.close(fd)
    try:
        shutil.copy2(app, temporary)
        with open(temporary, "rb") as copied:
            os.fsync(copied.fileno())
        os.replace(temporary, original)
        directory_fd = os.open(original.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def apply_pipeline(
    app_path: str,
    patches_dir: str,
    validator: Optional[Validator] = None,
) -> PipelineResult:
    validator = validator or validate_javascript
    app = Path(app_path)
    original = Path(str(app) + ".orig")
    lock_path = Path(str(app) + ".patch.lock")
    applied: list[str] = []
    failed: list[str] = []

    print("copilot-patch:", file=sys.stderr)
    lock_path.touch(exist_ok=True)
    with lock_path.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)

        ensure_original(app, original)

        fd, stage = tempfile.mkstemp(
            prefix=app.name + ".pipeline.",
            suffix=".mjs",
            dir=app.parent,
        )
        os.close(fd)
        shutil.copy2(original, stage)

        try:
            for patch in patch_files(patches_dir):
                fd, candidate = tempfile.mkstemp(
                    prefix=app.name + ".candidate.",
                    suffix=".mjs",
                    dir=app.parent,
                )
                os.close(fd)
                shutil.copy2(stage, candidate)
                try:
                    proc = subprocess.run(
                        [str(patch), candidate],
                        capture_output=True,
                        text=True,
                    )
                except OSError as exc:
                    failed.append(patch.name)
                    print(
                        f"  FAILED   {patch.name} — could not execute: {exc}",
                        file=sys.stderr,
                    )
                    cleanup_candidate(candidate)
                    continue
                valid, validation_detail = validator(candidate)
                if proc.returncode == 0 and valid:
                    os.replace(candidate, stage)
                    cleanup_candidate(candidate)
                    applied.append(patch.name)
                    print(f"  APPLIED  {patch.name}", file=sys.stderr)
                    continue

                failed.append(patch.name)
                detail = (
                    last_detail(proc)
                    if proc.returncode != 0
                    else validation_detail
                )
                print(f"  FAILED   {patch.name} — {detail}", file=sys.stderr)
                cleanup_candidate(candidate)

            valid, detail = validator(stage)
            if not valid:
                print(
                    f"  RESULT   publish failed — {detail}; live bundle unchanged",
                    file=sys.stderr,
                )
                return PipelineResult(applied, failed, False)

            os.replace(stage, app)
            print(
                f"  RESULT   {len(applied)} applied, {len(failed)} failed",
                file=sys.stderr,
            )
            return PipelineResult(applied, failed, True)
        finally:
            cleanup_candidate(stage)


def main(argv: list[str]) -> int:
    repo = Path(__file__).resolve().parent.parent
    app_path = argv[1] if len(argv) > 1 else find_app_js()
    patches_dir = argv[2] if len(argv) > 2 else str(repo / "patches")
    if not app_path or not os.path.isfile(app_path):
        print(
            f"copilot-patch: ERROR app.js not found: {app_path or '<unresolved>'}",
            file=sys.stderr,
        )
        return 1
    result = apply_pipeline(app_path, patches_dir)
    return 0 if result.published else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
