#!/usr/bin/env python3
"""Fail when a prospective public release contains obvious private artifacts."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


FORBIDDEN_PARTS = {
    "config.json",
    ".local-tools",
    "original",
    "decrypted",
    "extracted",
    "build",
    "repatch",
    "vendor",
    "__pycache__",
}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
)


def candidate_paths(root: Path) -> list[Path]:
    process = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / item.decode() for item in process.stdout.split(b"\0") if item]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--max-bytes", type=int, default=5_000_000)
    args = parser.parse_args()
    root = args.root.resolve()
    failures: list[str] = []
    paths = candidate_paths(root)
    for path in paths:
        relative = path.relative_to(root)
        if any(part in FORBIDDEN_PARTS for part in relative.parts):
            failures.append(f"forbidden path: {relative}")
            continue
        if not path.is_file():
            continue
        if path.stat().st_size > args.max_bytes:
            failures.append(f"large file: {relative} ({path.stat().st_size} bytes)")
            continue
        if relative.as_posix() == "tools/check_public_release.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                failures.append(f"possible secret in: {relative}")
                break
    if failures:
        print("public release check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"public release check passed: {len(paths)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
