#!/usr/bin/env python3
"""Pass the image-generation opt-in to Copilot's native ACP session factory."""

import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from apply_patches import ensure_original, validate_javascript  # noqa: E402
from find_app_js import find_app_js  # noqa: E402


MARKER = "/*copilot-patch:acp-image-generation*/"
BOOT = re.compile(
    r'function [\w$]+\([^()]*\)\{[^{};]*;return\{clientKind:"acp",'
    r"(?P<body>[^;]{0,4000}?)(?P<end>,homeDirectory:[^{};]*\}\})"
)


def build_patch(source: str) -> str:
    matches = list(BOOT.finditer(source))
    if len(matches) != 1:
        raise LookupError(f"expected one ACP boot options anchor, found {len(matches)}")
    match = matches[0]
    body = match.group("body")
    settings = re.search(
        r"contextTier:[^,;{}]*\?\?([\w$]+)\?\.contextTier", body
    )
    if settings is None:
        raise LookupError("ACP settings anchor not found")
    option = (
        MARKER
        + "imageGeneration:{enabled:"
        + settings.group(1)
        + "?.imageGeneration!==!1},"
    )
    if MARKER in source:
        if source.count(MARKER) == 1 and body.startswith(option):
            return source
        raise LookupError("incomplete or unexpected image-generation patch marker")
    if re.search(r"\bimageGeneration\s*:", body):
        raise LookupError("ACP already defines imageGeneration; inspect upstream behavior")
    offset = match.start("body")
    return source[:offset] + option + source[offset:]


def publish(path: Path, source: str) -> None:
    fd, name = tempfile.mkstemp(
        prefix=path.name + ".patching.", suffix=".mjs", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(source)
        shutil.copymode(path, temporary)
        valid, detail = validate_javascript(str(temporary))
        if not valid:
            raise ValueError(f"JavaScript validation failed: {detail}")
        ensure_original(path, Path(str(path) + ".orig"))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    target = argv[1] if len(argv) > 1 else find_app_js()
    if not target or not Path(target).is_file():
        print("acp-image-generation: ERROR bundle not found", file=sys.stderr)
        return 1
    path = Path(target)
    try:
        source = path.read_text(encoding="utf-8")
        patched = build_patch(source)
        if patched != source:
            publish(path, patched)
    except (OSError, LookupError, ValueError) as error:
        print(f"acp-image-generation: ERROR {error}; bundle unchanged", file=sys.stderr)
        return 2
    print("acp-image-generation: verified ACP image-generation option", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
