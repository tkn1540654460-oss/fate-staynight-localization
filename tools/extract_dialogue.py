#!/usr/bin/env python3
"""Extract translatable Fate/HuneX dialogue instructions as UTF-8 JSONL.

The input files are the decompressed script buffers produced by prep_tpl.py
(`*.ini`).  Output records keep the original instruction index so translated
text can later be applied without guessing which repeated sentence was meant.
Undecodable engine bytes are represented by ``{{RAW:XXXX}}`` tokens.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable


TEXT_COMMAND = re.compile(rb"^_(ZM[0-9A-Za-z]+|MSAD|MSA2|SELR)\((.*)$", re.DOTALL)
RUBY = re.compile(r"<([^,<>]+),[^<>]+>")
RAW_TOKEN = re.compile(r"\{\{RAW:[0-9A-F]+\}\}")


def decode_template(data: bytes) -> str:
    """Decode CP932 while making non-text engine bytes explicit and lossless."""
    result: list[str] = []
    index = 0
    while index < len(data):
        byte = data[index]
        if byte < 0x80:
            result.append(chr(byte))
            index += 1
            continue
        if 0xA1 <= byte <= 0xDF:
            result.append(bytes((byte,)).decode("cp932"))
            index += 1
            continue
        if (0x81 <= byte <= 0x9F or 0xE0 <= byte <= 0xFC) and index + 1 < len(data):
            pair = data[index : index + 2]
            try:
                result.append(pair.decode("cp932"))
            except UnicodeDecodeError:
                result.append("{{RAW:" + pair.hex().upper() + "}}")
            index += 2
            continue
        result.append(f"{{{{RAW:{byte:02X}}}}}")
        index += 1
    return "".join(result)


def plain_text(template: str) -> str:
    """Return a model-friendly reading string without engine formatting."""
    text = RAW_TOKEN.sub("", template)
    text = RUBY.sub(lambda match: match.group(1), text)
    text = text.replace("@n", "\n").replace("^", "\n")
    return "\n".join(part.strip(" \u3000") for part in text.splitlines()).strip()


def iter_records(path: Path) -> Iterable[dict[str, object]]:
    page = 0
    sequence = 0
    for instruction_index, instruction in enumerate(path.read_bytes().split(b";")):
        match = TEXT_COMMAND.match(instruction)
        if not match:
            if instruction.startswith(b"_TPG0("):
                page += 1
            continue
        command = match.group(1).decode("ascii")
        template = decode_template(match.group(2))
        readable = plain_text(template)
        if not readable:
            continue
        sequence += 1
        yield {
            "id": f"{path.stem}:{instruction_index:05d}",
            "script": path.stem,
            "instruction_index": instruction_index,
            "sequence": sequence,
            "page": page,
            "command": command,
            "source_template": template,
            "source_text": readable,
            "translation": None,
        }


def input_paths(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    return sorted(source.glob("*.ini"), key=lambda path: path.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="A decompressed .ini file or directory")
    parser.add_argument("output", type=Path, help="UTF-8 JSONL output")
    args = parser.parse_args()

    paths = input_paths(args.source)
    if not paths:
        raise ValueError(f"no .ini scripts found in {args.source}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with args.output.open("w", encoding="utf-8") as stream:
        for path in paths:
            for record in iter_records(path):
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
    print(f"extracted {count} records from {len(paths)} script(s) -> {args.output}")


if __name__ == "__main__":
    main()
