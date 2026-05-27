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

    FastLZ is one of the compression algorithms used in FST VCDATA blocks.
    This is a direct port of fastlz_decompress() from fastlz.c.
    """
    ip = 0
    ip_limit = len(src)
    ip_bound = ip_limit - 2
    op = 0
    op_limit = maxout
    out = bytearray(maxout)

    while True:
        # Process literal run
        ctrl = src[ip] & 31
        ip += 1
        if ctrl < 32:
            ctrl += 1
            if ctrl >= 32:
                while True:
                    if ip >= ip_limit:
                        raise FstFormatError("truncated FastLZ literal")
                    b = src[ip]
                    ip += 1
                    ctrl += b
                    if b != 255:
                        break
            if op + ctrl > op_limit:
                raise FstFormatError("FastLZ output overflow on literal")
            if ip + ctrl > ip_limit:
                raise FstFormatError("truncated FastLZ literal payload")
            out[op:op + ctrl] = src[ip:ip + ctrl]
            ip += ctrl
            op += ctrl

        if ip >= ip_bound:
            break

        # Process back-reference
        ofs = src[ip] | (src[ip + 1] << 8)
        ip += 2

        if ofs == 0:
            break

        ref = op - ofs
        if ref < 0:
            raise FstFormatError(f"invalid FastLZ offset {ofs}")

        # Match length
        ctrl = src[ip - 2] >> 5
        if ctrl == 7:
            ctrl += 1
            while True:
                if ip >= ip_limit:
                    raise FstFormatError("truncated FastLZ match length")
                b = src[ip]
                ip += 1
                ctrl += b
                if b != 255:
                    break

        ctrl += 2

        if op + ctrl > op_limit:
            ctrl = op_limit - op
        for _ in range(ctrl):
            out[op] = out[ref]
            op += 1
            ref += 1

    return bytes(out[:op])


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

