"""HuneX MZX0 codec.

Algorithm adapted from Hintay/PS-HuneX_Tools (MIT), which in turn documents
its decompressor as based on ExtractData's Mzx.cpp.
"""

from __future__ import annotations

import io
import struct


def decompress(blob: bytes, *, xor_ff: bool = True) -> bytes:
    if len(blob) < 8 or blob[:4] != b"MZX0":
        raise ValueError("not an MZX0 stream")
    expected = struct.unpack_from("<I", blob, 4)[0]
    source = io.BytesIO(blob[8:])
    output = io.BytesIO()
    ring = [b"\xFF\xFF" if xor_ff else b"\0\0"] * 64
    ring_pos = 0
    clear_count = 0
    last = b"\xFF\xFF" if xor_ff else b"\0\0"

    while output.tell() < expected and source.tell() < len(blob) - 8:
        if clear_count <= 0:
            clear_count = 0x1000
            last = b"\xFF\xFF" if xor_ff else b"\0\0"
        raw_flag = source.read(1)
        if not raw_flag:
            break
        flag = raw_flag[0]
        clear_count -= 1 if flag & 3 == 2 else flag // 4 + 1
        if flag & 3 == 0:
            output.write(last * (flag // 4 + 1))
        elif flag & 3 == 1:
            distance_raw = source.read(1)
            if not distance_raw:
                raise ValueError("truncated MZX0 back-reference")
            distance = 2 * (distance_raw[0] + 1)
            for _ in range(flag // 4 + 1):
                if output.tell() < distance:
                    raise ValueError("invalid MZX0 back-reference")
                output.seek(-distance, io.SEEK_CUR)
                last = output.read(2)
                output.seek(0, io.SEEK_END)
                output.write(last)
        elif flag & 3 == 2:
            last = ring[flag // 4]
            output.write(last)
        else:
            for _ in range(flag // 4 + 1):
                pair = source.read(2)
                if len(pair) != 2:
                    raise ValueError("truncated MZX0 literal")
                if xor_ff:
                    pair = bytes(byte ^ 0xFF for byte in pair)
                last = pair
                ring[ring_pos] = pair
                ring_pos = (ring_pos + 1) % 64
                output.write(pair)

    result = output.getvalue()[:expected]
    if len(result) != expected:
        raise ValueError(f"MZX0 length mismatch: expected {expected}, got {len(result)}")
    return result


def compress_literal(data: bytes, *, xor_ff: bool = True) -> bytes:
    """Create a valid literal-only MZX0 stream (larger but deterministic)."""
    output = bytearray(b"MZX0" + struct.pack("<I", len(data)))
    cursor = 0
    key32 = 0xFFFFFFFF if xor_ff else 0
    while len(data) - cursor >= 0x80:
        output.append(0xFF)
        for _ in range(0x20):
            value = struct.unpack_from("<I", data, cursor)[0] ^ key32
            output.extend(struct.pack("<I", value))
            cursor += 4
    remaining = len(data) - cursor
    key8 = 0xFF if xor_ff else 0
    even = remaining // 2 * 2
    if even:
        output.append(((even // 2) - 1) * 4 + 3)
        output.extend(byte ^ key8 for byte in data[cursor : cursor + even])
        cursor += even
    if cursor < len(data):
        output.append(0x03)
        output.append(data[cursor] ^ key8)
        output.append(0)
    return bytes(output)

