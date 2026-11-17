"""Reads a TIFF's directory sizes from its header, so Pillow never opens a crafted one.

Pillow builds an entry in memory for every strip or tile, and every directory entry,
when it opens a TIFF; millions of them cost gigabytes before any size check can run.
This reads only the counts, a few bytes each, from the first two directories (the only
ones read_file touches).
"""

from __future__ import annotations

import struct

# Far above any real scan: the largest plate, one strip per row, has 8,818.
MAX_STRIPS = 100_000
# Real scans carry a few dozen tags per directory.
MAX_ENTRIES = 1_000
_STRIP_OFFSETS, _TILE_OFFSETS = 273, 324
_DIRECTORIES = 2
# Classic TIFF (42) and BigTIFF (43): offset and value-count format, where the first
# directory's offset sits, a directory's entry-count format, and one entry's size.
_LAYOUTS = {42: ("I", 4, "H", 12), 43: ("Q", 8, "Q", 20)}


def tiff_problem(data: bytes) -> str | None:
    """Why this TIFF must not be opened, or None (also for anything not a TIFF)."""
    order = {b"II": "<", b"MM": ">"}.get(data[:2])
    if order is None or len(data) < 16:
        return None
    version = _read(data, order + "H", 2)
    if version not in _LAYOUTS:
        return None
    size_fmt, offset_at, count_fmt, entry_size = _LAYOUTS[version]
    offset_fmt, count_fmt = order + size_fmt, order + count_fmt
    count_size = struct.calcsize(count_fmt)
    offset = _read(data, offset_fmt, offset_at)
    for _ in range(_DIRECTORIES):
        if not offset or offset >= len(data):
            return None
        entries = _read(data, count_fmt, offset)
        if entries is None:
            return None
        if entries > MAX_ENTRIES:
            return f"a directory with {entries} entries"
        start = offset + count_size
        for i in range(entries):
            entry = start + i * entry_size
            tag = _read(data, order + "H", entry)
            if tag in (_STRIP_OFFSETS, _TILE_OFFSETS):
                count = _read(data, offset_fmt, entry + 4)
                if count is not None and count > MAX_STRIPS:
                    return f"{count} strips or tiles"
        offset = _read(data, offset_fmt, start + entries * entry_size)
    return None


def _read(data: bytes, fmt: str, at: int) -> int | None:
    size = struct.calcsize(fmt)
    if at < 0 or at + size > len(data):
        return None
    return struct.unpack_from(fmt, data, at)[0]
