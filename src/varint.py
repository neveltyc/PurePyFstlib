"""
Variable-length integer encoding as used by the FST format.

FST uses a little-endian-ish 7-bit varint format where low 7 bits come
first with continuation in the high bit, then the value is reconstructed
by iterating the bytes backwards (fstapi.c's fstGetVarint32/fstGetVarint64).
"""

from __future__ import annotations

from .common import FstFormatError


def read_varint(buf: bytes | bytearray | memoryview, off: int = 0) -> tuple[int, int]:
    """Read an unsigned varint. Returns (value, bytes_consumed).

    FST varint format: bytes are stored with continuation bit (bit 7) set
    for all but the last byte. The LAST byte (without continuation bit)
    contains the LEAST significant 7 bits. Reconstruction reads backwards
    from the last byte to the first.
    """
    start = off
    n = len(buf)
    while off < n and (buf[off] & 0x80):
        off += 1
    if off >= n:
        raise FstFormatError("truncated varint")
    # off now points to the last byte (which has bit7=0)
    end = off  # last byte index
    off += 1   # skip past

    value = 0
    # Iterate backwards: from last byte to first
    for i in range(end, start - 1, -1):
        value = (value << 7) | (buf[i] & 0x7F)
    return value, off - start


def read_varint32(buf: bytes | bytearray | memoryview, off: int = 0) -> tuple[int, int]:
    """Read an unsigned 32-bit varint. Alias for read_varint."""
    return read_varint(buf, off)


def read_varint64(buf: bytes | bytearray | memoryview, off: int = 0) -> tuple[int, int]:
    """Read an unsigned 64-bit varint. Same encoding as read_varint."""
    return read_varint(buf, off)


def read_svarint(buf: bytes | bytearray | memoryview, off: int = 0) -> tuple[int, int]:
    """Read a signed varint (zig-zag encoded)."""
    val, used = read_varint(buf, off)
    # zig-zag decode
    ret = val >> 1
    if val & 1:
        ret = -ret
    return ret, used


def read_svarint64(buf: bytes | bytearray | memoryview, off: int = 0) -> tuple[int, int]:
    """Read a signed 64-bit varint."""
    return read_svarint(buf, off)


def peek_varint32(buf: bytes | bytearray | memoryview, off: int = 0) -> int:
    """Read a varint value without advancing the offset."""
    val, _ = read_varint(buf, off)
    return val


def write_varint(value: int) -> bytes:
    """Encode an unsigned integer as an FST varint."""
    if value < 0:
        raise ValueError("varint must be non-negative")
    if value == 0:
        return b"\x00"
    parts = []
    while value:
        parts.append(value & 0x7F)
        value >>= 7
    # Set continuation bits, then reverse
    result = bytearray(len(parts))
    for i in range(len(parts)):
        b = parts[i]
        if i > 0:
            b |= 0x80
        result[len(parts) - 1 - i] = b
    return bytes(result)


def write_varint32(value: int) -> bytes:
    """Encode a 32-bit integer as FST varint bytes."""
    return write_varint(value)


def write_varint64(value: int) -> bytes:
    """Encode a 64-bit integer as FST varint bytes."""
    return write_varint(value)

