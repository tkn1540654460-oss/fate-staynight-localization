#!/usr/bin/env python3
"""Create a reproducible metadata manifest without embedding game data."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--title-id", required=True)
    parser.add_argument("--view", choices=("encrypted", "decrypted"), required=True)
    args = parser.parse_args()

    files = []
    for path in sorted(p for p in args.source.rglob("*") if p.is_file()):
        stat = path.stat()
        files.append(
            {
                "path": path.relative_to(args.source).as_posix(),
                "size": stat.st_size,
                "sha256": sha256(path),
            }
        )

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "title_id": args.title_id,
        "source_view": args.view,
        "file_count": len(files),
        "total_size": sum(item["size"] for item in files),
        "files": files,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

