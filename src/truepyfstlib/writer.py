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
    FST_BL_HDR, FST_BL_VCDATA, FST_BL_GEOM, FST_BL_HIER, FST_BL_BLACKOUT,
    FST_ST_GEN_ATTRBEGIN, FST_ST_GEN_ATTREND,
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


@dataclass
class _VcSection:
    records: list
    begin_time: int
    end_time: int
    frame_snapshot: dict


class FstWriter:

    def __init__(
        self,
        path: str | Path,
        start_time: int = 0,
        timescale: int = 0,
        version: str = "PurePyFstlib 0.1.0",
        date: str = "",
        filetype: int = FstFileType.VERILOG,
    ):
        self.path = Path(path)
        self.start_time = start_time
        self.timescale = timescale
        self.version = version
        self.date = date or _time.strftime("%Y-%m-%d %H:%M:%S")
        self.filetype = filetype
        self._handle_counter = 0
        self._var_count = 0
        self._vars_by_handle: dict[int, list[_VarInfo]] = {}
        self._handle_info: dict[int, _VarInfo] = {}
        self._scope_stack: list[tuple[str, str]] = []
        self._scope_count = 0
        self._hier_events: list[bytes] = []
        self._vc_records: list[_VcRecord] = []
        self._blackouts: list[tuple[int, bool]] = []
        self._current_time: int = start_time
        self._section_begin_time: int = start_time
        self._section_initial_values: dict[int, bytes] = {}
        self._hierarchy_frozen: bool = False
        self._end_time: int = start_time
        self._closed = False
        self._sections: list[_VcSection] = []
        self._current_values: dict[int, bytes] = {}

    def set_timescale(self, ts: int) -> None:
        self.timescale = ts

    def set_version(self, version: str) -> None:
        self.version = version

    def set_date(self, date: str) -> None:
        self.date = date

    def set_file_type(self, ft: int) -> None:
        self.filetype = ft

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("FstWriter is already closed")

    def _check_hierarchy_open(self) -> None:
        self._ensure_open()
        if self._hierarchy_frozen:
            raise RuntimeError(
                "hierarchy is frozen: cannot declare variables/scopes "
                "after writing value changes"
            )

    def _freeze_hierarchy(self) -> None:
        self._hierarchy_frozen = True

    def set_scope(self, scope_type: int, name: str, component: str = "") -> None:
        self._check_hierarchy_open()
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
        self._check_hierarchy_open()
        if self._scope_stack:
            self._scope_stack.pop()
        self._hier_events.append(bytes([FST_ST_VCD_UPSCOPE]))

    def set_attr_begin(self, attr_type: int, subtype: int,
                        name: str, arg: int = 0) -> None:
        self._check_hierarchy_open()
        if not isinstance(attr_type, int) or attr_type < 0:
            raise ValueError(f"attr_type must be non-negative int, got {attr_type!r}")
        if not isinstance(subtype, int) or subtype < 0:
            raise ValueError(f"subtype must be non-negative int, got {subtype!r}")
        buf = bytearray()
        buf.append(FST_ST_GEN_ATTRBEGIN)
        buf.append(attr_type)
        buf.append(subtype)
        buf.extend(name.encode("utf-8") + b"\x00")
        buf.extend(write_varint(arg))
        self._hier_events.append(bytes(buf))

    def set_attr_end(self) -> None:
        self._check_hierarchy_open()
        self._hier_events.append(bytes([FST_ST_GEN_ATTREND]))

    def emit_dump_active(self, enable: bool) -> None:
        self._freeze_hierarchy()
        self._blackouts.append((self._current_time, enable))

    def flush_context(self) -> None:
        self._freeze_hierarchy()
        if not self._vc_records:
            return
        # frame_snapshot captures the section"s BEGINNING state
        self._sections.append(
            _VcSection(
                records=list(self._vc_records),
                begin_time=self._section_begin_time,
                end_time=self._current_time,
                frame_snapshot=dict(self._section_initial_values),
            )
        )
        self._vc_records.clear()
        # Next section starts at current time, inheriting current values
        self._section_begin_time = self._current_time
        self._section_initial_values = dict(self._current_values)

    def create_var(
        self,
        var_type: int,
        direction: int,
        length: int,
        name: str,
        alias_handle: int = 0,
        is_string: bool = False,
    ) -> int:
        self._check_hierarchy_open()
        REAL_TYPES = {
            FstVarType.VCD_REAL, FstVarType.VCD_REAL_PARAMETER,
            FstVarType.VCD_REALTIME, FstVarType.SV_SHORTREAL,
        }
        if var_type in REAL_TYPES:
            raise NotImplementedError(
                "real-valued FST variables are not supported by writer yet"
            )
        if var_type == FstVarType.GEN_STRING:
            if is_string and length not in (0, None):
                raise ValueError("GEN_STRING variables must have length 0")
            is_string = True
            length = 0
        if not is_string and length <= 0:
            raise ValueError("non-string variables must have positive length")
        self._var_count += 1
        if alias_handle == 0:
            self._handle_counter += 1
            handle = self._handle_counter
            info = _VarInfo(
                var_type=var_type, direction=direction, name=name,
                length=length, alias_handle=0, is_string=is_string,
            )
            self._handle_info[handle] = info
            self._vars_by_handle.setdefault(handle, []).append(info)
            # init current value
            if is_string:
                initial = b""
            else:
                initial = b"0" * length
            self._current_values[handle] = initial
            self._section_initial_values[handle] = initial
        else:
            if alias_handle not in self._handle_info:
                raise KeyError(f"unknown alias handle: {alias_handle}")
            base = self._handle_info[alias_handle]
            if var_type != base.var_type:
                raise ValueError(
                    f"alias var_type ({var_type}) must match canonical ({base.var_type})"
                )
            if length != base.length:
                raise ValueError(
                    f"alias length ({length}) must match canonical ({base.length})"
                )
            if is_string != base.is_string:
                raise ValueError(
                    f"alias is_string ({is_string}) must match canonical ({base.is_string})"
                )
            handle = alias_handle
            # Use canonical metadata, only name differs
            alias_info = _VarInfo(
                var_type=base.var_type, direction=direction, name=name,
                length=base.length, alias_handle=alias_handle,
                is_string=base.is_string,
            )
            self._vars_by_handle.setdefault(handle, []).append(alias_info)
        buf = bytearray()
        buf.append(var_type)
        buf.append(direction)
        buf.extend(name.encode("utf-8") + b"\x00")
        buf.extend(write_varint(length))
        buf.extend(write_varint(0 if alias_handle == 0 else handle))
        self._hier_events.append(bytes(buf))
        return handle

    def emit_time_change(self, time: int) -> None:
        self._freeze_hierarchy()
        if time < self._current_time:
            raise ValueError("time must be monotonically increasing")
        self._current_time = time
        if time > self._end_time:
            self._end_time = time

    def _validate_handle(self, handle: int, value: bytes) -> _VarInfo:
        if handle not in self._handle_info:
            raise KeyError(f"unknown FST handle: {handle}")
        info = self._handle_info[handle]
        if not info.is_string:
            if info.length <= 1:
                if value not in (b"0", b"1", b"x", b"z", b"h", b"u", b"w", b"l", b"-", b"?"):
                    raise ValueError(
                        f"invalid 1-bit value for handle {handle}: {value!r}"
                    )
            elif len(value) != info.length:
                raise ValueError(
                    f"value length {len(value)} != signal width {info.length} "
                    f"for handle {handle}"
                )
        return info

    def emit_value_change(self, handle: int, value: bytes) -> None:
        self._ensure_open()
        if isinstance(value, str):
            value = value.encode("utf-8")
        info = self._validate_handle(handle, value)
        is_string = info.is_string
        self._vc_records.append(_VcRecord(
            time_delta=self._current_time, handle=handle, value=value,
            is_string=is_string,
        ))
        self._current_values[handle] = value

    def emit_value_change_bit(self, handle: int, bit: int) -> None:
        self._ensure_open()
        if bit not in (0, 1):
            raise ValueError("bit must be 0 or 1")
        self.emit_value_change(handle, b"1" if bit else b"0")

    def close(self) -> None:
        self._ensure_open()
        if self._closed:
            return
        self._closed = True
        data = self._build_file()
        self.path.write_bytes(data)

    def _build_file(self) -> bytes:
        result = bytearray()
        # Snapshot any pending section
        if self._vc_records:
            self._sections.append(
                _VcSection(
                    records=list(self._vc_records),
                    begin_time=self._section_begin_time,
                    end_time=self._end_time,
                    frame_snapshot=dict(self._section_initial_values),
                )
            )
        elif not self._sections and self._handle_counter > 0:
            # No value changes but variables exist: create time-zero frame-only section
            self._sections.append(
                _VcSection(
                    records=[],
                    begin_time=self.start_time,
                    end_time=self.start_time,
                    frame_snapshot=dict(self._section_initial_values),
                )
            )
        # vc_section_count
        self._vc_section_count = len(self._sections)
        hdr = self._build_header()
        geom_blk = self._build_geometry_block()
        hier_blk = self._build_hierarchy_block()
        result.extend(self._wrap_block(FST_BL_HDR, hdr))
        result.extend(self._wrap_block(FST_BL_GEOM, geom_blk))
        result.extend(self._wrap_block(FST_BL_HIER, hier_blk))
        for section_idx, section in enumerate(self._sections):
            for vc_blk in self._build_vc_sections(section, section_idx):
                result.extend(vc_blk)
        # Blackout block (after VCDATA sections, per fstapi format)
        if self._blackouts:
            result.extend(self._build_blackout_block())
        return bytes(result)

    def _build_header(self) -> bytes:
        # vc_section_count must match the number of VCDATA blocks written.
        vc_section_count = self._vc_section_count if self._vc_section_count > 0 else (1 if self._handle_counter > 0 else 0)
        buf = bytearray()
        buf.extend(struct.pack(">Q", self.start_time))
        buf.extend(struct.pack(">Q", self._end_time))
        buf.extend(struct.pack("<d", FST_DOUBLE_ENDTEST))
        buf.extend(struct.pack(">Q", 0))
        buf.extend(struct.pack(">Q", self._scope_count))
        buf.extend(struct.pack(">Q", self._var_count))
        buf.extend(struct.pack(">Q", self._handle_counter))
        buf.extend(struct.pack(">Q", vc_section_count))
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
        # Geometry varint convention used by the libfst reader
        # (fstapi.c:4781-4791) and writer (fstapi.c:2688-2695):
        #     0          → 8-byte FST_VT_VCD_REAL (double)
        #     0xFFFFFFFF → variable-length string (FST_VT_GEN_STRING)
        #     N (other)  → N-bit signal
        # The previous implementation had the two special cases swapped, so a
        # var created with is_string=True wrote geom=0 (reader thinks: real,
        # length 8), and a normal var declared with length=0 wrote
        # geom=0xFFFFFFFF (reader thinks: string). Anyone calling create_var
        # with is_string=True would see fst2vcd emit an empty `$var string 0`
        # with no dumped values.
        geom_data = bytearray()
        for h in range(1, self._handle_counter + 1):
            vi = self._handle_info.get(h)
            if vi is None:
                geom_data.extend(write_varint(0xFFFFFFFF))  # unknown → treat as string
            elif vi.is_string:
                geom_data.extend(write_varint(0xFFFFFFFF))
            elif vi.length == 0:
                geom_data.extend(write_varint(0))            # real
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

    def _build_vc_sections(self, section=None, section_idx=0) -> list[bytes]:
        if section is None:
            return []
        all_records = list(section.records)
        if not all_records:
            # Empty section with variables: emit time-0 frame-only section
            frame_data = bytearray()
            for h in range(1, self._handle_counter + 1):
                vi = self._handle_info.get(h)
                if vi is None:
                    continue
                if h in section.frame_snapshot:
                    val = section.frame_snapshot[h]
                    if not vi.is_string:
                        frame_data.extend(val)
                else:
                    if not vi.is_string:
                        frame_data.extend(b"0" * vi.length)
            frame_bytes = bytes(frame_data)
            frame_compressed = zlib.compress(frame_bytes)
            block_body = bytearray()
            block_body.extend(struct.pack(">Q", section.begin_time))
            block_body.extend(struct.pack(">Q", section.end_time))
            block_body.extend(struct.pack(">Q", 0))
            block_body.extend(write_varint(len(frame_bytes)))
            block_body.extend(write_varint(len(frame_compressed)))
            block_body.extend(write_varint(self._handle_counter))
            block_body.extend(frame_compressed)
            block_body.extend(write_varint(self._handle_counter))
            block_body.append(ord("Z"))
            # empty chain table
            chain_cmem = self._build_chain_table([0] * self._handle_counter, [False] * self._handle_counter)
            block_body.extend(chain_cmem)
            block_body.extend(struct.pack(">Q", len(chain_cmem)))
            # empty time table
            time_comp = zlib.compress(b"")
            block_body.extend(time_comp)
            block_body.extend(struct.pack(">Q", 0))
            block_body.extend(struct.pack(">Q", len(time_comp)))
            block_body.extend(struct.pack(">Q", 0))
            total_len = 8 + len(block_body)
            hdr = bytearray()
            hdr.append(FST_BL_VCDATA)
            hdr.extend(struct.pack(">Q", total_len))
            return [bytes(hdr) + bytes(block_body)]
        times = sorted(set(r.time_delta for r in all_records))
        section_begin = section.begin_time
        section_end = section.end_time
        max_handle = self._handle_counter

        # frame_data layout per libfst convention (fstapi.c:5208-5350 reader,
        # fstapi.c:2702-2714 writer):
        #
        #   string      (geom = 0xFFFFFFFF, sig_len = 0)  → 0 bytes
        #   real        (geom = 0,           sig_len = 8) → 8 bytes (NaN)
        #   N-bit wire  (geom = N,           sig_len = N) → N bytes (init "x"/"0")
        #
        # Previously every is_string OR length==0 var wrote exactly 1 byte,
        # which misaligned every signal after a string. C reader still ran but
        # fst2vcd's `b<chars>` output for the next multi-bit signal contained
        # the stray 0x00 byte at offset 0, which truncates printf and silently
        # drops subsequent dumpvars lines.
        frame_data = bytearray()
        for h in range(1, max_handle + 1):
            vi = self._handle_info.get(h)
            if vi is None:
                # No canonical var for this handle
                continue
            elif h in section.frame_snapshot:
                snap = section.frame_snapshot[h]
                if vi.is_string:
                    continue
                frame_data.extend(snap)
                continue
            if False:
                pass  # dead, keep indent structure
                # Unknown handle: treat as 1-bit wire with '0' initial.
                frame_data.append(0x30)
            elif vi.is_string:
                # Strings have no initial value; contribute 0 bytes.
                continue
            elif vi.length == 0:
                # Real (FST_VT_VCD_REAL): 8 bytes of NaN, like the C writer at
                # fstapi.c:2711.  IEEE-754 double NaN = 7FF8000000000000 (BE).
                frame_data.extend(b"\x7f\xf8\x00\x00\x00\x00\x00\x00")
            else:
                # N-bit wire: write N ASCII '0' chars. C writer uses 'x'; '0'
                # is also valid VCD and matches our existing convention.
                frame_data.extend(b"0" * vi.length)
        frame_bytes = bytes(frame_data)
        frame_compressed = zlib.compress(frame_bytes)

        # Build per-handle VC chunks with correct offsets
        handle_chunks: dict[int, bytes] = {}
        for h in range(1, max_handle + 1):
            vi = self._handle_info.get(h)
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
            time_to_index = {t: i for i, t in enumerate(times)}
            chunk = bytearray()
            prev_tdelta = 0
            for rec in handle_records:
                abs_tdelta = time_to_index[rec.time_delta]
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
        signals_present: list[bool] = []
        for h in range(1, max_handle + 1):
            chunk = handle_chunks.get(h, b"")
            chunk_offsets.append(len(vc_payload))
            signals_present.append(bool(chunk))
            if chunk:
                compressed = zlib.compress(chunk)
                if len(compressed) < len(chunk):
                    vc_payload.extend(write_varint(len(chunk)))
                    vc_payload.extend(compressed)
                else:
                    # Uncompressed (compressed larger than raw)
                    vc_payload.append(0)
                    vc_payload.extend(chunk)
        chain_cmem = self._build_chain_table(chunk_offsets, signals_present)

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
        block_body.extend(struct.pack(">Q", section_begin))
        block_body.extend(struct.pack(">Q", section_end))
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

    def _build_chain_table(self, chunk_offsets: list[int],
                            signals_present: list[bool]) -> bytes:
        """Build chain table per the libfst block-1 / DYN_ALIAS encoding.

        Three varint shapes recognised by the reader:
          - `(loopcnt << 1)`             (bit 0 = 0): N consecutive empty
                                          signals -- chain_table[i] = 0,
                                          reader yields only the frame value.
          - `(delta << 1) | 1`           (bit 0 = 1): signal *has* data;
                                          chain_table[i] = previous + delta.
                                          The first such delta is the absolute
                                          offset from `vc_start` (= position
                                          of the pack_type byte).
          - `0; varint(-len)`            (alias-to-prior): unused by this
                                          writer because we never share data.

        Without the zero-skip case, signals declared via create_var but never
        emitted got a `(0 << 1) | 1 = 1` entry, which the C reader at
        `fstapi.c:5457` interpreted as "another chunk at the same offset".
        It then went to read that chunk's varint header, walked into the
        chain data, and aborted (`fst2vcd: Could not open ...`).

        The trailing sentinel is still computed by the reader as
        `indx_pos - vc_start`; we do NOT emit it.
        """
        result = bytearray()
        pval = 0
        zerocnt = 0
        for i, has_data in enumerate(signals_present):
            if not has_data:
                zerocnt += 1
                continue
            if zerocnt:
                result.extend(write_varint(zerocnt << 1))
                zerocnt = 0
            abs_off = chunk_offsets[i] + 1  # +1 for pack_type byte
            delta = abs_off - pval
            result.extend(write_varint((delta << 1) | 1))
            pval = abs_off
        if zerocnt:
            result.extend(write_varint(zerocnt << 1))
        return bytes(result)

    def _build_blackout_block(self) -> bytes:
        if not self._blackouts:
            return b""
        body = bytearray()
        body.extend(write_varint(len(self._blackouts)))
        prev_time = 0
        for t, active in self._blackouts:
            delta = t - prev_time
            prev_time = t
            body.append(1 if active else 0)
            body.extend(write_varint(delta))
        return self._wrap_block(FST_BL_BLACKOUT, bytes(body))

    @staticmethod
    def _wrap_block(block_type: int, body: bytes) -> bytes:
        buf = bytearray()
        buf.append(block_type)
        buf.extend(struct.pack(">Q", 8 + len(body)))
        buf.extend(body)
        return bytes(buf)

