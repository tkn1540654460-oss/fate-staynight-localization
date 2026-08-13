#!/usr/bin/env python3
"""Merge validated translation checkpoints into the extraction JSONL format."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Iterable


def load_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def checkpoint_paths(directory: Path) -> list[Path]:
    paths = list(directory.glob("*.json"))
    paths.extend(directory.parent.glob("gemini_*.json"))
    return sorted(set(paths))


def load_translations(paths: Iterable[Path]) -> dict[str, dict[str, object]]:
    translations: dict[str, dict[str, object]] = {}
    origins: dict[str, Path] = {}
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            identifier = item.get("id")
            translation = item.get("translation")
            if not isinstance(identifier, str) or not isinstance(translation, str):
                continue
            previous = translations.get(identifier)
            if previous is not None and previous["translation"] != translation:
                raise ValueError(
                    f"conflicting translations for {identifier}: "
                    f"{origins[identifier]} and {path}"
                )
            translations[identifier] = {
                "translation": translation,
                "notes": item.get("notes", ""),
            }
            origins[identifier] = path
    return translations


def atomic_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        temporary = Path(stream.name)
    temporary.replace(path)


def merge(source: Path, checkpoint_dir: Path, output: Path) -> tuple[int, int]:
    records = load_jsonl(source)
    source_ids = {str(record["id"]) for record in records}
    translations = load_translations(checkpoint_paths(checkpoint_dir))
    unknown = set(translations) - source_ids
    if unknown:
        raise ValueError(f"checkpoint contains {len(unknown)} unknown record ids")
    missing = source_ids - set(translations)
    if missing:
        return len(translations), len(missing)
    merged: list[dict[str, object]] = []
    for record in records:
        item = dict(record)
        translated = translations[str(record["id"])]
        item["translation"] = translated["translation"]
        item["notes"] = translated["notes"]
        merged.append(item)
    atomic_jsonl(output, merged)
    return len(merged), 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()

    last_completed = -1
    while True:
        completed, missing = merge(args.source, args.checkpoint_dir, args.output)
        if completed != last_completed:
            print(
                f"merge status: completed={completed} missing={missing}",
                flush=True,
            )
            last_completed = completed
        if missing == 0:
            print(f"merged {completed} records -> {args.output}", flush=True)
            return
        if not args.wait:
            raise SystemExit(2)
        time.sleep(max(1, args.poll_seconds))


if __name__ == "__main__":
    main()
