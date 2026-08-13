#!/usr/bin/env python3
"""Lossless-oriented unpacker/repacker for HuneX mrgd00 (MZP) archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path


SIGNATURE = b"mrgd00"
ALIGNMENT = 8
SECTOR = 0x800


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode_size(sector_span: int, low_size: int) -> int:
    if sector_span < 1:
        raise ValueError("invalid zero sector span")
    return ((sector_span - 1) // 0x20) * 0x10000 + low_size


def encode_span(within_sector: int, size: int) -> tuple[int, int]:
    low_size = size & 0xFFFF
    physical_span = max(1, math.ceil((within_sector + size) / SECTOR))
    size_hint_span = (size >> 16) * 0x20 + 1
    span = max(physical_span, size_hint_span)
    if span > 0xFFFF:
        raise ValueError(f"cannot encode entry size {size}")
    return span, low_size


def read_archive(path: Path) -> tuple[bytes, list[dict[str, object]]]:
    archive = path.read_bytes()
    if len(archive) < 8 or archive[:6] != SIGNATURE:
        raise ValueError(f"{path} is not an mrgd00 archive")
    count = struct.unpack_from("<H", archive, 6)[0]
    data_start = 8 + count * 8
    if data_start > len(archive):
        raise ValueError("descriptor table exceeds archive size")
    descriptors = []
    for index in range(count):
        sector_offset, offset, sector_span, low_size = struct.unpack_from(
            "<HHHH", archive, 8 + index * 8
        )
        absolute = data_start + sector_offset * SECTOR + offset
        descriptors.append((sector_offset, offset, sector_span, low_size, absolute))

    entries: list[dict[str, object]] = []
    for index, (sector_offset, offset, sector_span, low_size, absolute) in enumerate(descriptors):
        size = decode_size(sector_span, low_size)
        boundary = descriptors[index + 1][4] if index + 1 < count else len(archive)
        # The high-size hint in sector_span is ambiguous when low_size is near
        # 0xffff. Reduce wrapped 64 KiB blocks if they would overlap the next
        # sequential member (observed in Fate's font archives).
        while size > low_size and absolute + size > boundary:
            size -= 0x10000
        end = absolute + size
        if absolute < data_start or end > len(archive):
            raise ValueError(f"entry {index} exceeds archive size")
        data = archive[absolute:end]
        entries.append(
            {
                "index": index,
                "offset": absolute,
                "size": size,
                "sha256": digest(data),
                "data": data,
            }
        )
    return archive, entries


def internal_names(entries: list[dict[str, object]]) -> dict[int, str]:
    if not entries:
        return {}
    table = entries[0]["data"]
    assert isinstance(table, bytes)
    names: dict[int, str] = {0: "allscr.nam", 1: "unknownX.mrg", 2: "unknownX2.mrg"}
    for name_index in range(len(table) // 32):
        raw = table[name_index * 32 : (name_index + 1) * 32]
        raw = raw.split(b"\0", 1)[0].replace(b"\x01", b"")
        if not raw:
            continue
        names[name_index + 3] = raw.decode("cp932", errors="replace") + ".mzx"
    return names


def unpack(source: Path, output: Path) -> None:
    archive, entries = read_archive(source)
    output.mkdir(parents=True, exist_ok=True)
    names = internal_names(entries)
    manifest_entries = []
    for entry in entries:
        index = int(entry["index"])
        file_name = f"{index:04d}.bin"
        data = entry.pop("data")
        assert isinstance(data, bytes)
        (output / file_name).write_bytes(data)
        manifest_entries.append(
            {
                **entry,
                "file": file_name,
                "internal_name": names.get(index),
            }
        )
    manifest = {
        "schema_version": 1,
        "format": "HuneX mrgd00",
        "source_size": len(archive),
        "source_sha256": digest(archive),
        "entries": manifest_entries,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def pack(source: Path, output: Path) -> None:
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    records = sorted(manifest["entries"], key=lambda item: item["index"])
    if len(records) > 0xFFFF or [r["index"] for r in records] != list(range(len(records))):
        raise ValueError("manifest entry indexes must be contiguous")

    data_start = 8 + len(records) * 8
    cursor = data_start
    descriptors = bytearray()
    payload = bytearray()
    for position, record in enumerate(records):
        data = (source / record["file"]).read_bytes()
        if position == 0:
            aligned = cursor
        else:
            # HuneX starts every following member at the next 8-byte boundary,
            # including a full 8-byte separator when the prior member ended aligned.
            aligned = (cursor // ALIGNMENT + 1) * ALIGNMENT
        payload.extend(b"\xFF" * (aligned - cursor))
        cursor = aligned
        relative = cursor - data_start
        sector_offset, within = divmod(relative, SECTOR)
        span, low_size = encode_span(within, len(data))
        if sector_offset > 0xFFFF:
            raise ValueError("archive exceeds the format's offset limit")
        descriptors.extend(struct.pack("<HHHH", sector_offset, within, span, low_size))
        payload.extend(data)
        cursor += len(data)

    # Archives also terminate at the *next* boundary, retaining a full padding
    # block when the final member itself ends aligned.
    final_size = (cursor // ALIGNMENT + 1) * ALIGNMENT
    payload.extend(b"\xFF" * (final_size - cursor))

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(SIGNATURE + struct.pack("<H", len(records)) + descriptors + payload)


def verify(path: Path) -> None:
    archive, entries = read_archive(path)
    print(f"entries={len(entries)} size={len(archive)} sha256={digest(archive)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    unpack_cmd = commands.add_parser("unpack")
    unpack_cmd.add_argument("source", type=Path)
    unpack_cmd.add_argument("output", type=Path)
    pack_cmd = commands.add_parser("pack")
    pack_cmd.add_argument("source", type=Path)
    pack_cmd.add_argument("output", type=Path)
    verify_cmd = commands.add_parser("verify")
    verify_cmd.add_argument("source", type=Path)
    args = parser.parse_args()
    if args.command == "unpack":
        unpack(args.source, args.output)
    elif args.command == "pack":
        pack(args.source, args.output)
    else:
        verify(args.source)


if __name__ == "__main__":
    main()
