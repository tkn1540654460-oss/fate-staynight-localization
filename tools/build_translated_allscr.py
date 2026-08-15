#!/usr/bin/env python3
"""Inject translated templates by stable instruction ID and rebuild allscr.mrg."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from collections import defaultdict
from pathlib import Path

from extract_dialogue import TEXT_COMMAND, decode_template
from mzp import pack, unpack
from mzx_codec import compress_literal, decompress


RAW_TOKEN = re.compile(r"\{\{RAW:([0-9A-Fa-f]+)\}\}")
LABEL_RECORD_SIZE = 32
LABEL_NAME_SIZE = 21
LABEL_RECORD = re.compile(
    rb"^(?P<name>.{21})(?P<label_id>[0-9A-Fa-f]{3}),(?P<offset>[0-9A-Fa-f]{5})\r\n$"
)


def encode_template(text: str, aliases: dict[str, bytes]) -> bytes:
    output = bytearray()
    cursor = 0
    for match in RAW_TOKEN.finditer(text):
        for character in text[cursor : match.start()]:
            output.extend(aliases.get(character) or character.encode("cp932"))
        raw = bytes.fromhex(match.group(1))
        if not raw:
            raise ValueError("empty RAW token")
        output.extend(raw)
        cursor = match.end()
    for character in text[cursor:]:
        output.extend(aliases.get(character) or character.encode("cp932"))
    return bytes(output)


def rebuild_label_offsets(unpack_dir: Path, manifest: dict[str, object]) -> int:
    """Update Fate's per-script label table after translated text changes size.

    Outer member 1 (unknownX.mrg) contains one inner member for each script,
    starting at outer member 3.  Every 32-byte record stores a label name,
    label id, and the byte offset of the corresponding ``_ZZ...`` command in
    the decompressed script.  Keeping the original offsets makes _JUMP land in
    unrelated text or the following debug menu after translation changes the
    script length.
    """
    outer_entries = {
        int(item["index"]): item for item in manifest["entries"]
    }
    label_member = outer_entries.get(1)
    if label_member is None:
        raise ValueError("allscr label archive (outer member 1) is absent")

    label_archive = unpack_dir / str(label_member["file"])
    with tempfile.TemporaryDirectory(prefix="fate-labels-") as directory:
        label_dir = Path(directory)
        unpack(label_archive, label_dir)
        label_manifest = json.loads(
            (label_dir / "manifest.json").read_text(encoding="utf-8")
        )
        updated = 0
        for label_entry in label_manifest["entries"]:
            label_index = int(label_entry["index"])
            label_path = label_dir / str(label_entry["file"])
            table = label_path.read_bytes()
            if not table:
                continue
            if len(table) % LABEL_RECORD_SIZE:
                raise ValueError(
                    f"label table {label_index} has invalid size {len(table)}"
                )

            script_member = outer_entries.get(label_index + 3)
            if script_member is None:
                raise ValueError(f"script for label table {label_index} is absent")
            script_path = unpack_dir / str(script_member["file"])
            script = decompress(script_path.read_bytes())

            rebuilt = bytearray()
            for start in range(0, len(table), LABEL_RECORD_SIZE):
                record = table[start : start + LABEL_RECORD_SIZE]
                match = LABEL_RECORD.match(record)
                if match is None:
                    raise ValueError(
                        f"label table {label_index} has malformed record at {start}"
                    )
                name_field = match.group("name")
                name = name_field.rstrip(b" ")
                label_id = match.group("label_id")
                markers = (
                    b"_ZZ" + label_id + b"01(*" + name,
                    b"_ZY" + label_id + b"00(" + name,
                )
                positions = []
                for marker in markers:
                    cursor = 0
                    while True:
                        position = script.find(marker, cursor)
                        if position < 0:
                            break
                        positions.append(position)
                        cursor = position + 1
                if len(positions) != 1:
                    raise ValueError(
                        f"label {label_index}:{name.decode('ascii')} matched "
                        f"{len(positions)} script positions"
                    )
                offset = positions[0]
                if offset > 0xFFFFF:
                    raise ValueError(
                        f"label {label_index}:{name.decode('ascii')} offset exceeds 20 bits"
                    )
                rebuilt.extend(
                    name_field
                    + label_id
                    + b","
                    + f"{offset:05x}".encode("ascii")
                    + b"\r\n"
                )
                updated += 1
            label_path.write_bytes(bytes(rebuilt))
        pack(label_dir, label_archive)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("translation", type=Path)
    parser.add_argument("alias_plan", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument(
        "--reference-dir",
        type=Path,
        help="directory containing the fully reconstructed *.mzx member names",
    )
    args = parser.parse_args()

    aliases_payload = json.loads(args.alias_plan.read_text(encoding="utf-8"))
    aliases = {
        item["character"]: bytes.fromhex(item["bytes"])
        for item in aliases_payload["aliases"]
    }
    records: dict[str, list[dict[str, object]]] = defaultdict(list)
    with args.translation.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            records[row["script"]].append(row)

    context = tempfile.TemporaryDirectory(prefix="fate-allscr-") if args.work_dir is None else None
    root = args.work_dir if args.work_dir is not None else Path(context.name)
    root.mkdir(parents=True, exist_ok=True)
    unpack_dir = root / "unpacked"
    unpack(args.source, unpack_dir)
    manifest = json.loads((unpack_dir / "manifest.json").read_text(encoding="utf-8"))
    # allscr.nam shortens many repeated route prefixes, so its raw member names
    # are not stable identifiers.  Match the previously reconstructed named
    # scripts back to archive members by their original content hashes instead.
    reference_dir = args.reference_dir or args.source.with_name("allscr-unpacked")
    by_hash: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in manifest["entries"]:
        by_hash[item["sha256"]].append(item)

    patched = 0
    changed_scripts = 0
    for script, script_records in records.items():
        internal_name = script + ".mzx"
        reference = reference_dir / internal_name
        if not reference.is_file():
            raise ValueError(f"reference script is absent: {reference}")
        reference_hash = hashlib.sha256(reference.read_bytes()).hexdigest()
        matches = by_hash.get(reference_hash, [])
        if len(matches) != 1:
            raise ValueError(
                f"script hash is not uniquely mapped in allscr: "
                f"{internal_name} ({len(matches)} matches)"
            )
        member = matches[0]
        member_path = unpack_dir / member["file"]
        plain = decompress(member_path.read_bytes())
        instructions = plain.split(b";")
        for row in script_records:
            index = int(row["instruction_index"])
            if index >= len(instructions):
                raise ValueError(f"{row['id']}: instruction index exceeds script")
            instruction = instructions[index]
            match = TEXT_COMMAND.match(instruction)
            if match is None:
                raise ValueError(f"{row['id']}: target is not a text instruction")
            command = match.group(1).decode("ascii")
            if command != row["command"]:
                raise ValueError(f"{row['id']}: command changed: {command}")
            current_template = decode_template(match.group(2))
            if current_template != row["source_template"]:
                raise ValueError(f"{row['id']}: source template mismatch")
            encoded = encode_template(row["translation"], aliases)
            instructions[index] = instruction[: match.start(2)] + encoded
            patched += 1
        rebuilt_plain = b";".join(instructions)
        rebuilt = compress_literal(rebuilt_plain)
        if decompress(rebuilt) != rebuilt_plain:
            raise ValueError(f"{script}: MZX readback mismatch")
        member_path.write_bytes(rebuilt)
        changed_scripts += 1

    if patched != sum(map(len, records.values())):
        raise ValueError("not every translation record was patched")
    updated_labels = rebuild_label_offsets(unpack_dir, manifest)
    pack(unpack_dir, args.output)
    print(
        json.dumps(
            {
                "patched_records": patched,
                "changed_scripts": changed_scripts,
                "updated_labels": updated_labels,
                "output": str(args.output),
                "size": args.output.stat().st_size,
            },
            ensure_ascii=False,
        )
    )
    if context is not None:
        context.cleanup()


if __name__ == "__main__":
    main()
