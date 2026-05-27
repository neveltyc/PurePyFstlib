"""
Pure-Python FST waveform writer.

Implements a basic FST writer matching the libfst writer API pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import gzip
import io
import struct
import time as _time
import zlib

from .common import (
    FstBlockType, FstScopeType, FstVarType, FstVarDir, FstFileType,
    FstWriterPackType,
    FST_BL_HDR, FST_BL_VCDATA, FST_BL_GEOM, FST_BL_HIER,
    FST_ST_VCD_SCOPE, FST_ST_VCD_UPSCOPE,
    FST_HDR_SIM_VERSION_SIZE, FST_HDR_DATE_SIZE, FST_DOUBLE_ENDTEST,
    FST_RCV_STR,
    FstVar, FstScope, FstFormatError,
)
from .varint import write_varint


@dataclass
class _VarInfo:
    var_type: int
    direction: int
    name: str
    length: int
    alias_handle: int
    is_string: bool = False


@dataclass
class _VcRecord:
    time_delta: int
    handle: int
    value: bytes
    is_string: bool = False


class FstWriter:

    def __init__(
        self,
        path: str | Path,
        start_time: int = 0,
        timescale: int = 0,
        version: str = "PurePyFstlib 0.1.0",
        date: str = "",
        filetype: int = FstFileType.VERILOG,
        use_compressed_hier: bool = True,
        pack_type: int = FstWriterPackType.ZLIB,
    ):
        self.path = Path(path)
        self.start_time = start_time
        self.timescale = timescale
        self.version = version
        self.date = date or _time.strftime("%Y-%m-%d %H:%M:%S")
        self.filetype = filetype
        self.use_compressed_hier = use_compressed_hier
        self.pack_type = pack_type
        self._handle_counter = 0
        self._vars: dict[int, _VarInfo] = {}
        self._scope_stack: list[tuple[str, str]] = []
        self._scope_count = 0
        self._hier_events: list[bytes] = []
        self._vc_records: list[_VcRecord] = []
        self._current_time: int = start_time
        self._end_time: int = start_time
        self._closed = False

    def set_timescale(self, ts: int) -> None:
        self.timescale = ts

    def set_version(self, version: str) -> None:
        self.version = version

    def set_date(self, date: str) -> None:
        self.date = date

    def set_file_type(self, ft: int) -> None:
        self.filetype = ft

    def set_scope(self, scope_type: int, name: str, component: str = "") -> None:
        component = component or name
        self._scope_stack.append((name, component))
        self._scope_count += 1
        buf = bytearray()
        buf.append(FST_ST_VCD_SCOPE)
        buf.append(scope_type)
        buf.extend(name.encode("utf-8") + b"\x00")
        buf.extend(component.encode("utf-8") + b"\x00")
        self._hier_events.append(bytes(buf))

    def set_upscope(self) -> None:
        if self._scope_stack:
            self._scope_stack.pop()
        self._hier_events.append(bytes([FST_ST_VCD_UPSCOPE]))

    def create_var(
        self,
        var_type: int,
        direction: int,
        length: int,
        name: str,
        alias_handle: int = 0,
        is_string: bool = False,
    ) -> int:
        if alias_handle == 0:
            self._handle_counter += 1
            handle = self._handle_counter
        else:
            handle = alias_handle
        self._vars[handle] = _VarInfo(
            var_type=var_type, direction=direction, name=name,
            length=length, alias_handle=alias_handle, is_string=is_string,
        )
        buf = bytearray()
        buf.append(var_type)
        buf.append(direction)
        buf.extend(name.encode("utf-8") + b"\x00")
        buf.extend(write_varint(length))
        buf.extend(write_varint(0 if alias_handle == 0 else handle))
        self._hier_events.append(bytes(buf))
        return handle

    def emit_time_change(self, time: int) -> None:
        if time < self._current_time:
            raise ValueError("time must be monotonically increasing")
        self._current_time = time
        if time > self._end_time:
            self._end_time = time

    def emit_value_change(self, handle: int, value: bytes) -> None:
        self._vc_records.append(_VcRecord(
            time_delta=self._current_time, handle=handle, value=value,
        ))

    def emit_value_change_bit(self, handle: int, bit: int) -> None:
        self.emit_value_change(handle, bytes([0x30 | (bit & 1)]))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        data = self._build_file()
        self.path.write_bytes(data)

    def _build_file(self) -> bytes:
        result = bytearray()
        hdr = self._build_header()
        geom_blk = self._build_geometry_block()
        hier_blk = self._build_hierarchy_block()
        result.extend(self._wrap_block(FST_BL_HDR, hdr))
        result.extend(self._wrap_block(FST_BL_GEOM, geom_blk))
        result.extend(self._wrap_block(FST_BL_HIER, hier_blk))
        for vc_blk in self._build_vc_sections():
            result.extend(vc_blk)
        return bytes(result)

    def _build_header(self) -> bytes:
        buf = bytearray()
        buf.extend(struct.pack(">Q", self.start_time))
        buf.extend(struct.pack(">Q", self._end_time))
        buf.extend(struct.pack("<d", FST_DOUBLE_ENDTEST))
        buf.extend(struct.pack(">Q", 0))
        buf.extend(struct.pack(">Q", self._scope_count))
        buf.extend(struct.pack(">Q", self._handle_counter))
        buf.extend(struct.pack(">Q", self._handle_counter))
        buf.extend(struct.pack(">Q", 1))
        ts_byte = self.timescale & 0xFF
        if ts_byte >= 128:
            ts_byte -= 256
        buf.append(ts_byte & 0xFF)
        ver_bytes = self.version.encode("utf-8")[:FST_HDR_SIM_VERSION_SIZE]
        buf.extend(ver_bytes.ljust(FST_HDR_SIM_VERSION_SIZE, b"\x00"))
        date_bytes = self.date.encode("utf-8")[:FST_HDR_DATE_SIZE]
        buf.extend(date_bytes.ljust(FST_HDR_DATE_SIZE, b"\x00"))
        buf.append(self.filetype & 0xFF)
        buf.extend(struct.pack(">Q", 0))
        return bytes(buf)

    def _build_geometry_block(self) -> bytes:
        geom_data = bytearray()
        for h in range(1, self._handle_counter + 1):
            vi = self._vars.get(h)
            if vi is None or vi.is_string:
                geom_data.extend(write_varint(0))
            elif vi.length == 0:
                geom_data.extend(write_varint(0xFFFFFFFF))
            else:
                geom_data.extend(write_varint(vi.length))
        compressed = zlib.compress(bytes(geom_data))
        buf = bytearray()
        buf.extend(struct.pack(">Q", len(geom_data)))
        buf.extend(struct.pack(">Q", self._handle_counter))
        buf.extend(compressed)
        return bytes(buf)

    def _build_hierarchy_block(self) -> bytes:
        # libfst reads FST_BL_HIER with gzdopen() which expects gzip format
        # (not raw zlib). zlib.compress() produces zlib format and causes
        # gzread() to return raw bytes without decompressing, hanging fst2vcd.
        # Use mtime=0 for byte-deterministic output.
        raw = b"".join(self._hier_events)
        buf_compress = io.BytesIO()
        with gzip.GzipFile(fileobj=buf_compress, mode="wb", mtime=0) as gz:
            gz.write(raw)
        compressed = buf_compress.getvalue()
        buf = bytearray()
        buf.extend(struct.pack(">Q", len(raw)))
        buf.extend(compressed)
        return bytes(buf)

    def _build_vc_sections(self) -> list[bytes]:
        if not self._vc_records:
            return []
        all_records = list(self._vc_records)
        times = sorted(set(r.time_delta for r in all_records))
        max_handle = self._handle_counter

        frame_data = bytearray()
        for h in range(1, max_handle + 1):
            vi = self._vars.get(h)
            if vi is None:
                frame_data.append(0x30)
            elif vi.is_string or vi.length == 0:
                frame_data.append(0)
            else:
                frame_data.extend(b"0" * vi.length)
        frame_bytes = bytes(frame_data)
        frame_compressed = zlib.compress(frame_bytes)

        # Build per-handle VC chunks with correct offsets
        handle_chunks: dict[int, bytes] = {}
        for h in range(1, max_handle + 1):
            vi = self._vars.get(h)
            if vi is None:
                handle_chunks[h] = b""
                continue
            handle_records = sorted(
                [r for r in all_records if r.handle == h],
                key=lambda r: r.time_delta,
            )
            if not handle_records:
                handle_chunks[h] = b""
                continue
            chunk = bytearray()
            prev_tdelta = 0
            for rec in handle_records:
                abs_tdelta = times.index(rec.time_delta)
                cum_tdelta = abs_tdelta - prev_tdelta
                prev_tdelta = abs_tdelta
                if rec.is_string:
                    raw = rec.value
                    buf = bytearray()
                    buf.extend(write_varint((cum_tdelta << 1) | 0))
                    buf.extend(write_varint(len(raw)))
                    buf.extend(raw)
                    chunk.extend(buf)
                elif vi.length <= 1:
                    val_byte = rec.value[0] if rec.value else 0x30
                    if val_byte == 0x30 or val_byte == ord("0"):
                        vli = (cum_tdelta << 2)
                    elif val_byte == 0x31 or val_byte == ord("1"):
                        vli = (cum_tdelta << 2) | (1 << 1)
                    else:
                        # x/z/h/u/w/l/-/? encoding.  Reader (`iter_value_changes`)
                        # extracts the index via `(vli >> 1) & 7`, so the layout
                        # is: bit 0 = 1 (marks non-0/1 value), bits 1..3 = idx
                        # into FST_RCV_STR, bits 4+ = tdelta.  The previous
                        # `| (1 << 1)` always forced bit 1 high, so idx 0 (x)
                        # decoded to idx 1 (z), etc.
                        idx = FST_RCV_STR.find(val_byte)
                        if idx < 0:
                            idx = 7
                        vli = (cum_tdelta << 4) | (idx << 1) | 1
                    chunk.extend(write_varint(vli))
                else:
                    chunk.extend(write_varint((cum_tdelta << 1) | 1))
                    chunk.extend(rec.value)
            handle_chunks[h] = bytes(chunk)

        # Concatenate VC payloads and build chain table
        vc_payload = bytearray()
        chunk_offsets: list[int] = []
        for h in range(1, max_handle + 1):
            chunk = handle_chunks.get(h, b"")
            chunk_offsets.append(len(vc_payload))
            if chunk:
                # Prepend varint(0) = uncompressed marker
                vc_payload.append(0)
                vc_payload.extend(chunk)
        chain_cmem = self._build_chain_table(chunk_offsets, len(vc_payload))

        # Time table
        time_table_raw = bytearray()
        prev = 0
        for t in times:
            delta = t - prev
            time_table_raw.extend(write_varint(delta))
            prev = t
        time_table_compressed = zlib.compress(bytes(time_table_raw))

        # Build block
        indx_len = len(chain_cmem)  # chain table byte length
        # memory_required_for_traversal: total uncompressed bytes the reader
        # must allocate via malloc(value + 66) at fstapi.c:5081 to inflate all
        # per-signal chunks. With value=0 the reader allocates only 66 bytes
        # and overflows on any non-tiny file (heap corruption -> SIGABRT in
        # fst2vcd). The C writer accumulates this from each chunk's
        # uncompressed length (fstapi.c:1485). We currently store every chunk
        # uncompressed prefixed with a 1-byte varint(0) marker, so the
        # reader's destlen for chunk i = len(handle_chunks[h]).
        mem_required = sum(len(c) for c in handle_chunks.values())
        block_body = bytearray()
        block_body.extend(struct.pack(">Q", self.start_time))
        block_body.extend(struct.pack(">Q", self._end_time))
        block_body.extend(struct.pack(">Q", mem_required))
        block_body.extend(write_varint(len(frame_bytes)))
        block_body.extend(write_varint(len(frame_compressed)))
        block_body.extend(write_varint(max_handle))
        block_body.extend(frame_compressed)
        block_body.extend(write_varint(max_handle))
        block_body.append(ord("Z"))
        block_body.extend(vc_payload)
        block_body.extend(chain_cmem)
        block_body.extend(struct.pack(">Q", indx_len))
        block_body.extend(time_table_compressed)
        block_body.extend(struct.pack(">Q", len(time_table_raw)))
        block_body.extend(struct.pack(">Q", len(time_table_compressed)))
        block_body.extend(struct.pack(">Q", len(times)))
        total_len = 8 + len(block_body)
        header = bytearray()
        header.append(FST_BL_VCDATA)
        header.extend(struct.pack(">Q", total_len))
        return [bytes(header) + bytes(block_body)]

    def _build_chain_table(self, chunk_offsets: list[int], total_len: int) -> bytes:
        """Build chain table encoding byte offsets into VC data area.
        
        Offsets are relative to pack_type position (vc_start), NOT VC data start.
        Since VC data starts 1 byte after pack_type, we add 1 to all offsets.

        Important: the chain stream contains ONE varint per signal, no sentinel.
        libfst's reader computes the trailing sentinel as `indx_pos - vc_start`
        (fstapi.c:5474). Emitting an explicit sentinel here would make the
        reader write past chain_table[vc_maxhandle], which is sized
        calloc(vc_maxhandle+1, ...).
        """
        result = bytearray()
        pval = 0
        for i, off in enumerate(chunk_offsets):
            abs_off = off + 1  # +1 for pack_type byte
            if i == 0:
                result.extend(write_varint((abs_off << 1) | 1))
            else:
                delta = abs_off - pval
                result.extend(write_varint((delta << 1) | 1))
            pval = abs_off
        return bytes(result)

    @staticmethod
    def _wrap_block(block_type: int, body: bytes) -> bytes:
        buf = bytearray()
        buf.append(block_type)
        buf.extend(struct.pack(">Q", 8 + len(body)))
        buf.extend(body)
        return bytes(buf)

