"""
Pure-Python decompression routines for FST block encoding.

Supports:
  - LZ4 block decompression (used for hierarchy and VCDATA)
  - FastLZ decompression (one of the VCDATA pack types)
  - zlib (via stdlib)
"""

from __future__ import annotations

import zlib

from .common import FstFormatError


def lz4_decompress(src: bytes, expected_len: int | None = None) -> bytes:
    """Pure-Python LZ4 block decompressor.

    Matches the LZ4 block format used by libfst's hierarchy and VCDATA
    blocks. This is the raw block format, not framed .lz4.
    """
    i = 0
    n = len(src)
    out = bytearray()

    while i < n:
        token = src[i]
        i += 1

        # Literal length
        literal_len = token >> 4
        if literal_len == 15:
            while True:
                if i >= n:
                    raise FstFormatError("truncated LZ4 literal length")
                b = src[i]
                i += 1
                literal_len += b
                if b != 255:
                    break

        if i + literal_len > n:
            raise FstFormatError("truncated LZ4 literal payload")
        out.extend(src[i:i + literal_len])
        i += literal_len

        if i >= n:
            break

        # Match offset
        if i + 2 > n:
            raise FstFormatError("truncated LZ4 offset")
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0 or offset > len(out):
            raise FstFormatError(f"invalid LZ4 offset {offset}")

        # Match length
        match_len = token & 0x0F
        if match_len == 15:
            while True:
                if i >= n:
                    raise FstFormatError("truncated LZ4 match length")
                b = src[i]
                i += 1
                match_len += b
                if b != 255:
                    break
        match_len += 4

        # Copy match
        start = len(out) - offset
        for j in range(match_len):
            out.append(out[start + j])

    if expected_len is not None and len(out) != expected_len:
        raise FstFormatError(
            f"LZ4 decompressed length mismatch: got {len(out)}, expected {expected_len}"
        )
    return bytes(out)


def fastlz_decompress(src: bytes, maxout: int) -> bytes:
    """Pure-Python FastLZ level 1 decompressor.

    Direct port of fastlz1_decompress() from fastlz.c:418-547 (level-1
    branches only; level-2 is not used by libfst). FastLZ is one of the
    compression options for FST VCDATA chunks (pack_type 'F').

    Format (level 1):
      byte 0: low 5 bits = first ctrl; high 3 bits = level marker (discard).
      Thereafter, each ctrl byte branches:
        ctrl >= 32  back-reference.  length = (ctrl>>5)-1 + 3 bytes.
                    offset = ((ctrl & 31) << 8) | next_byte.
                    Copy from out[op - offset - 1] byte-by-byte
                    (overlap-safe; do NOT use a slice).
        ctrl <  32  literal: copy next (ctrl + 1) bytes verbatim.
      After each event, read the next full byte as ctrl.  Exit when
      input is exhausted.
    """
    if not src:
        return b""
    out = bytearray()
    ip = 0
    ip_limit = len(src)
    ctrl = src[ip] & 0x1F
    ip += 1
    loop = True
    while loop:
        if ctrl >= 32:
            length = (ctrl >> 5) - 1
            ofs = (ctrl & 0x1F) << 8
            if length == 6:
                if ip >= ip_limit:
                    raise FstFormatError("truncated FastLZ extended length")
                length += src[ip]
                ip += 1
            if ip >= ip_limit:
                raise FstFormatError("truncated FastLZ offset low byte")
            ref = len(out) - ofs - src[ip] - 1
            ip += 1
            if ref < 0:
                raise FstFormatError(
                    f"invalid FastLZ back-reference (ref={ref})"
                )
            if ip < ip_limit:
                ctrl = src[ip]
                ip += 1
            else:
                loop = False
            for _ in range(length + 3):
                out.append(out[ref])
                ref += 1
        else:
            count = ctrl + 1
            if ip + count > ip_limit:
                raise FstFormatError("truncated FastLZ literal payload")
            out.extend(src[ip:ip + count])
            ip += count
            if ip < ip_limit:
                ctrl = src[ip]
                ip += 1
            else:
                loop = False
    if maxout > 0 and len(out) > maxout:
        return bytes(out[:maxout])
    return bytes(out)


def decompress_zlib(data: bytes, expected_len: int | None = None) -> bytes:
    """Decompress zlib data, optionally checking expected length."""
    result = zlib.decompress(data)
    if expected_len is not None and len(result) != expected_len:
        raise FstFormatError(
            f"zlib decompressed length mismatch: got {len(result)}, expected {expected_len}"
        )
    return result


def decompress_block(data: bytes, pack_type: str,
                     expected_len: int | None = None) -> bytes:
    """Decompress according to FST pack type: 'Z'/'!'=zlib, '4'=LZ4, 'F'=FastLZ."""
    if pack_type in ('Z', '!'):
        return decompress_zlib(data, expected_len)
    elif pack_type == '4':
        return lz4_decompress(data, expected_len)
    elif pack_type == 'F':
        return fastlz_decompress(data, expected_len)
    else:
        raise FstFormatError(f"unknown FST pack type: {pack_type!r}")

