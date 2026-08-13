#!/usr/bin/env python3
"""Replace one exact CP932 text occurrence inside an MZX0 script."""

from __future__ import annotations

import argparse
from pathlib import Path

from mzx_codec import compress_literal, decompress


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("old")
    parser.add_argument("new")
    args = parser.parse_args()

    old = args.old.encode("cp932")
    new = args.new.encode("cp932")
    plain = decompress(args.source.read_bytes())
    count = plain.count(old)
    if count != 1:
        raise ValueError(f"expected exactly one match, found {count}")
    modified = plain.replace(old, new, 1)
    packed = compress_literal(modified)
    if decompress(packed) != modified:
        raise ValueError("MZX0 verification failed")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(packed)
    print(
        f"patched 1 occurrence; plain {len(plain)} -> {len(modified)} bytes; "
        f"MZX0 {args.source.stat().st_size} -> {len(packed)} bytes"
    )


if __name__ == "__main__":
    main()

