#!/usr/bin/env python3
"""Fail CI if tracked files contain common Telegram secrets or runtime data."""

from pathlib import Path
import re
import subprocess
import sys

TOKEN_PATTERN = re.compile(rb"(?<![A-Za-z0-9_])\d{8,12}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_])")
ALLOWED_ENV_FILES = {".env.example"}
BLOCKED_FILES = {".env", "progress.json"}
BLOCKED_PREFIXES = ("data/",)


def tracked_files():
    output = subprocess.check_output(["git", "ls-files", "-z"])
    return [Path(item.decode("utf-8")) for item in output.split(b"\0") if item]


def main():
    problems = []
    for path in tracked_files():
        normalized = path.as_posix()
        if normalized in BLOCKED_FILES or any(normalized.startswith(p) for p in BLOCKED_PREFIXES):
            problems.append(f"runtime data must not be tracked: {normalized}")
            continue
        if path.name.startswith(".env") and normalized not in ALLOWED_ENV_FILES:
            problems.append(f"environment secret file must not be tracked: {normalized}")
            continue
        try:
            content = path.read_bytes()
        except OSError as exc:
            problems.append(f"cannot read {normalized}: {exc}")
            continue
        if TOKEN_PATTERN.search(content):
            problems.append(f"Telegram token pattern found: {normalized}")

    if problems:
        print("\n".join(f"ERROR: {problem}" for problem in problems), file=sys.stderr)
        return 1

    print("Security checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
