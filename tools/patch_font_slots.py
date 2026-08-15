#!/usr/bin/env python3
"""Replace selected glyph cells in a HuneX 4bpp font MRG.

Requires Pillow. The initial PoC targets page 10, whose first cells are the
CP932/JIS glyphs 粛, 塾, 熟 in the Fate/stay night Vita font archives.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from mzp import pack, unpack
from mzx_codec import compress_literal, decompress


CELL = 36
COLUMNS = 14


def alpha_value(raw: int) -> int:
    return (raw << 1) + (raw >> 6) if raw < 0x80 else 255


def palette(meta: bytes) -> list[tuple[int, int, int, int]]:
    if len(meta) < 80 or meta[12:16] != b"\x01\x00\x00\x00":
        raise ValueError("unsupported font atlas metadata")
    result = []
    for index in range(16):
        r, g, b, raw_alpha = meta[16 + index * 4 : 20 + index * 4]
        result.append((r, g, b, alpha_value(raw_alpha)))
    return result


def closest(values: list[tuple[int, int, int, int]], target: tuple[int, int, int, int]) -> int:
    return min(
        range(len(values)),
        key=lambda index: sum((values[index][channel] - target[channel]) ** 2 for channel in range(4)),
    )


def render_glyph(character: str, font_path: Path, font_size: int) -> Image.Image:
    font = ImageFont.truetype(str(font_path), font_size)
    mask = Image.new("L", (CELL, CELL), 0)
    draw = ImageDraw.Draw(mask)
    bbox = draw.textbbox((0, 0), character, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = (CELL - width) // 2 - bbox[0]
    y = (CELL - height) // 2 - bbox[1]
    draw.text((x, y), character, font=font, fill=255)
    return mask


def patch_pixels(
    packed: bytes,
    meta: bytes,
    replacements: list[tuple[int, str]],
    font_path: Path,
    font_size: int,
) -> bytes:
    raw = bytearray(decompress(packed, xor_ff=False))
    width = int.from_bytes(meta[0:2], "little")
    height = int.from_bytes(meta[2:4], "little")
    backing_width = int.from_bytes(meta[4:6], "little")
    backing_height = int.from_bytes(meta[6:8], "little")
    if width != 512 or height != 504 or backing_width != 512 or backing_height != 512:
        raise ValueError("PoC expects a 512x512 backing atlas")
    if len(raw) != backing_width * backing_height // 2:
        raise ValueError("unexpected 4bpp atlas length")

    colors = palette(meta)
    transparent = closest(colors, (0, 0, 0, 0))
    white = closest(colors, (255, 255, 255, 255))
    has_colored_opaque = len({(r, g, b) for r, g, b, a in colors if a > 200}) > 1
    black = closest(colors, (0, 0, 0, 255))

    pixels = [0] * (backing_width * backing_height)
    for byte_index, value in enumerate(raw):
        pixels[byte_index * 2] = value & 0x0F
        pixels[byte_index * 2 + 1] = value >> 4

    for slot, character in replacements:
        cell_x = (slot % COLUMNS) * CELL
        cell_y = (slot // COLUMNS) * CELL
        mask = render_glyph(character, font_path, font_size)
        # The original 5x5 dilation produced a roughly two-pixel black border
        # around injected Chinese glyphs.  In a 36x36 cell that overwhelms the
        # white strokes and looks noticeably blockier than the native font.
        outline = mask.filter(ImageFilter.MaxFilter(3)) if has_colored_opaque else None
        for y in range(CELL):
            for x in range(CELL):
                coverage = mask.getpixel((x, y))
                if coverage >= 96:
                    value = white
                elif outline is not None and outline.getpixel((x, y)) >= 96:
                    value = black
                elif coverage:
                    value = closest(colors, (255, 255, 255, coverage))
                else:
                    value = transparent
                pixels[(cell_y + y) * backing_width + cell_x + x] = value

    output = bytearray(len(raw))
    for byte_index in range(len(output)):
        output[byte_index] = pixels[byte_index * 2] | (pixels[byte_index * 2 + 1] << 4)
    return compress_literal(bytes(output), xor_ff=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--font-size", type=int, default=32)
    parser.add_argument("--page", type=int, default=10)
    parser.add_argument(
        "--replace",
        action="append",
        required=True,
        metavar="SLOT=CHAR",
        help="glyph slot within the page, for example 0=这",
    )
    args = parser.parse_args()
    replacements = []
    for value in args.replace:
        slot_text, character = value.split("=", 1)
        if len(character) != 1:
            raise ValueError("each replacement must contain one character")
        replacements.append((int(slot_text), character))

    with tempfile.TemporaryDirectory(prefix="fate-font-") as directory:
        root = Path(directory)
        outer = root / "outer"
        nested = root / "nested"
        unpack(args.source, outer)
        page_file = outer / f"{args.page:04d}.bin"
        unpack(page_file, nested)
        meta = (nested / "0000.bin").read_bytes()
        packed = (nested / "0001.bin").read_bytes()
        (nested / "0001.bin").write_bytes(
            patch_pixels(packed, meta, replacements, args.font, args.font_size)
        )
        pack(nested, page_file)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        pack(outer, args.output)
    print(f"patched {args.source.name} -> {args.output}")


if __name__ == "__main__":
    main()
