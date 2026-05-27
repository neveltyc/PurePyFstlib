"""
Pure-Python FST waveform reader.

Supports:
  - Block scanning (all block types)
  - Header parsing
  - Geometry block (signal lengths)
  - Hierarchy block (scope/var tree, zlib/LZ4/LZ4DUO)
  - VCDATA value-change traversal with interleaved time/value iteration

Not yet implemented:
  - Dynamic alias VCDATA blocks (VCDATA_DYN_ALIAS, VCDATA_DYN_ALIAS2)
  - ZWRAPPER (whole-file gzip wrapper)
  - Blackout sections
  - Variable-length string type (FST_VT_GEN_STRING)
  - Parallel/hier file support
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
import struct
import zlib

from .common import (
    FstBlockType, FstHeader, FstScope, FstVar, FstUpscope,
    FstAttrBegin, FstAttrEnd, FstFormatError, FstBlock,
    FST_BL_HDR, FST_BL_VCDATA, FST_BL_BLACKOUT, FST_BL_GEOM,
    FST_BL_HIER, FST_BL_VCDATA_DYN_ALIAS, FST_BL_HIER_LZ4,
    FST_BL_HIER_LZ4DUO, FST_BL_VCDATA_DYN_ALIAS2, FST_BL_ZWRAPPER, FST_BL_SKIP,
    FST_ST_GEN_ATTRBEGIN, FST_ST_GEN_ATTREND,
    FST_ST_VCD_SCOPE, FST_ST_VCD_UPSCOPE, FST_VT_MAX,
    FST_HDR_SIM_VERSION_SIZE, FST_HDR_DATE_SIZE, FST_DOUBLE_ENDTEST,
    FST_RCV_STR,
)
from .varint import (
    read_varint, read_varint32, read_varint64,
    read_svarint, read_svarint64, peek_varint32,
)
from .compression import lz4_decompress


@dataclass
class VcSection:
    """Parsed value-change section metadata."""
    block_offset: int
    block_type: int
    section_length: int
    beg_time: int
    end_time: int
    times: list = None
    frame_uclen: int = 0
    frame_clen: int = 0
    frame_maxhandle: int = 0
    frame_data: bytes = b""
    vc_maxhandle: int = 0
    vc_start: int = 0
    pack_type: str = ""
    chain_table: list = None
    chain_table_lengths: list = None
    indx_pos: int = 0
    indx_len: int = 0


class FstReader:
    """Pure-Python reader for FST waveform files."""

    VCDATA_BLOCK_TYPES = {FST_BL_VCDATA, FST_BL_VCDATA_DYN_ALIAS, FST_BL_VCDATA_DYN_ALIAS2}

    def __init__(self, path: str | Path):
        self.path = Path(path)
        raw = self.path.read_bytes()
        # Handle ZWRAPPER (whole-file zlib compression)
        if raw and raw[0] == FST_BL_ZWRAPPER:
            if len(raw) < 17:
                raise FstFormatError("truncated ZWRAPPER")
            uclen = int.from_bytes(raw[9:17], "big")
            # Try gzip first, then raw deflate
            try:
                self._data = zlib.decompress(raw[17:], 15 + 32)
            except zlib.error:
                self._data = zlib.decompress(raw[17:], -15)
            if len(self._data) != uclen:
                raise FstFormatError("ZWRAPPER decompressed length mismatch")
        else:
            self._data = raw
        self._blocks = self._scan_blocks(self._data)
        self.header = self._parse_header()
        self._signal_lengths: list[int] = []
        self._signal_types: list[int] = []
        self._hierarchy_events: list = []
        self._vc_sections: list[VcSection] = []
        self._handle_to_var: dict[int, 'FstVar'] = {}
        self._parse_geometry_and_hierarchy()
        self._build_handle_map()
        self._parse_vc_sections()

    @staticmethod
    def _scan_blocks(data: bytes) -> list[FstBlock]:
        blocks: list[FstBlock] = []
        off = 0
        n = len(data)
        while off < n:
            if off + 9 > n:
                raise FstFormatError(f"truncated block header at offset {off}")
            block_type = data[off]
            section_length = _u64be(data, off + 1)
            end = off + 1 + section_length
            if section_length < 8 or end > n:
                raise FstFormatError(
                    f"invalid section length {section_length} at offset {off}"
                )
            payload = data[off + 9:end]
            blocks.append(FstBlock(off, block_type, section_length, payload))
            off = end
        return blocks

    def _parse_header(self) -> FstHeader:
        header_blocks = [b for b in self._blocks if b.block_type == FST_BL_HDR]
        if not header_blocks:
            raise FstFormatError("missing FST header block")
        b = header_blocks[0].payload
        if len(b) < 320:
            raise FstFormatError("truncated FST header payload")
        off = 0
        start_time = _u64be(b, off); off += 8
        end_time = _u64be(b, off); off += 8
        dcheck_raw = b[off:off + 8]; off += 8
        d_le = struct.unpack("<d", dcheck_raw)[0]
        d_be = struct.unpack(">d", dcheck_raw)[0]
        double_endian_match = abs(d_le - FST_DOUBLE_ENDTEST) < 1e-15
        if not double_endian_match and abs(d_be - FST_DOUBLE_ENDTEST) >= 1e-15:
            raise FstFormatError("invalid FST endian check double")
        memory_used_by_writer = _u64be(b, off); off += 8
        scope_count = _u64be(b, off); off += 8
        var_count = _u64be(b, off); off += 8
        max_handle = _u64be(b, off); off += 8
        vc_section_count = _u64be(b, off); off += 8
        timescale = _i8(b[off]); off += 1
        version = b[off:off + FST_HDR_SIM_VERSION_SIZE]
        version = version.split(b"\0", 1)[0].decode("utf-8", errors="replace")
        off += FST_HDR_SIM_VERSION_SIZE
        date = b[off:off + FST_HDR_DATE_SIZE]
        date = date.split(b"\0", 1)[0].decode("utf-8", errors="replace")
        off += FST_HDR_DATE_SIZE
        filetype = b[off] if off < len(b) else 0
        off += 1
        timezero = _u64be(b, off) if off + 8 <= len(b) else 0
        return FstHeader(
            start_time=start_time, end_time=end_time,
            double_endian_match=double_endian_match,
            memory_used_by_writer=memory_used_by_writer,
            scope_count=scope_count, var_count=var_count,
            max_handle=max_handle,
            value_change_section_count=vc_section_count,
            timescale=timescale, version=version, date=date,
            filetype=filetype, timezero=timezero,
        )

    def _parse_geometry(self, block: FstBlock) -> tuple[list[int], list[int]]:
        body = block.payload
        if len(body) < 16:
            raise FstFormatError("truncated geometry block")
        uclen = _u64be(body, 0)
        maxhandle = _u64be(body, 8)
        comp = body[16:]
        geom = comp if len(comp) == uclen else zlib.decompress(comp)
        if len(geom) != uclen:
            raise FstFormatError("geometry length mismatch")
        signal_lens: list[int] = []
        signal_typs: list[int] = []
        off = 0
        for _ in range(int(maxhandle)):
            val, used = read_varint(geom, off)
            off += used
            if val and val != 0xFFFFFFFF:
                signal_lens.append(val)
                signal_typs.append(16)
            elif val == 0xFFFFFFFF:
                signal_lens.append(0)
                signal_typs.append(16)
            else:
                signal_lens.append(8)
                signal_typs.append(3)
        return signal_lens, signal_typs

    def _extract_hierarchy(self) -> bytes:
        geom_blocks = [b for b in self._blocks if b.block_type == FST_BL_GEOM]
        if geom_blocks:
            self._signal_lengths, self._signal_types = \
                self._parse_geometry(geom_blocks[0])
        hier_blocks = [
            b for b in self._blocks
            if b.block_type in {FST_BL_HIER, FST_BL_HIER_LZ4, FST_BL_HIER_LZ4DUO}
        ]
        if not hier_blocks:
            raise FstFormatError("missing hierarchy block")
        block = hier_blocks[0]
        body = block.payload
        if len(body) < 8:
            raise FstFormatError("truncated hierarchy block")
        uclen = _u64be(body, 0)
        comp = body[8:]
        if block.block_type == FST_BL_HIER:
            try:
                data = zlib.decompress(comp)
            except zlib.error:
                data = zlib.decompress(comp, 15 + 32)  # try gzip
        elif block.block_type == FST_BL_HIER_LZ4:
            data = lz4_decompress(comp, uclen)
        elif block.block_type == FST_BL_HIER_LZ4DUO:
            uclen2, used = read_varint(comp, 0)
            mid = lz4_decompress(comp[used:], uclen2)
            data = lz4_decompress(mid, uclen)
        else:
            raise AssertionError(block.block_type)
        if len(data) != uclen:
            raise FstFormatError("hierarchy length mismatch")
        return data

    def _parse_hierarchy(self, data: bytes) -> list:
        events: list = []
        scopes: list[str] = []
        cur_scope = ""
        current_handle = 0
        off = 0
        n = len(data)
        while off < n:
            tag = data[off]
            off += 1
            if tag == FST_ST_VCD_SCOPE:
                if off >= n:
                    raise FstFormatError("truncated scope")
                scope_type = data[off]; off += 1
                name, off = _read_cstr(data, off)
                component, off = _read_cstr(data, off)
                full = name if not cur_scope else cur_scope + "." + name
                scopes.append(cur_scope)
                cur_scope = full
                events.append(FstScope(scope_type, name, component, full))
            elif tag == FST_ST_VCD_UPSCOPE:
                events.append(FstUpscope())
                if scopes:
                    cur_scope = scopes.pop()
                else:
                    cur_scope = ""
            elif tag == FST_ST_GEN_ATTRBEGIN:
                attr_type = data[off]; subtype = data[off + 1]; off += 2
                name, off = _read_cstr(data, off)
                arg, used = read_varint(data, off); off += used
                events.append(FstAttrBegin(attr_type, subtype, name, arg))
            elif tag == FST_ST_GEN_ATTREND:
                events.append(FstAttrEnd())
            elif 0 <= tag <= FST_VT_MAX:
                direction = data[off]; off += 1
                name, off = _read_cstr(data, off)
                length, used = read_varint(data, off); off += used
                alias, used = read_varint(data, off); off += used
                if alias == 0:
                    current_handle += 1
                    handle = current_handle
                    is_alias = False
                else:
                    handle = alias
                    is_alias = True
                full = name if not cur_scope else cur_scope + "." + name
                events.append(FstVar(tag, direction, name, length, handle, is_alias, full))
            elif tag == 0xFF and off == n:
                break
            else:
                raise FstFormatError(
                    f"unknown hierarchy tag 0x{tag:02x} at offset {off - 1}"
                )
        return events

    def _parse_geometry_and_hierarchy(self) -> None:
        hier_data = self._extract_hierarchy()
        self._hierarchy_events = self._parse_hierarchy(hier_data)

    def _build_handle_map(self) -> None:
        """Build handle->FstVar lookup dict."""
        for e in self._hierarchy_events:
            if isinstance(e, FstVar):
                self._handle_to_var[e.handle] = e

    @staticmethod
    def _is_vc_block(b: FstBlock) -> bool:
        return b.block_type in FstReader.VCDATA_BLOCK_TYPES

    def _parse_vc_sections(self) -> None:
        vc_blocks = [b for b in self._blocks if self._is_vc_block(b)]
        if not vc_blocks:
            return
        for block in vc_blocks:
            sect = VcSection(
                block_offset=block.offset,
                block_type=block.block_type,
                section_length=block.section_length,
                beg_time=0, end_time=0,
            )
            payload = block.payload
            off = 0
            if len(payload) < 24:
                raise FstFormatError("truncated VCDATA header")
            sect.beg_time = _u64be(payload, off); off += 8
            sect.end_time = _u64be(payload, off); off += 8
            off += 8
            frame_uclen, used = read_varint64(payload, off); off += used
            frame_clen, used2 = read_varint64(payload, off); off += used2
            frame_maxhandle, used3 = read_varint64(payload, off); off += used3
            sect.frame_uclen = frame_uclen
            sect.frame_clen = frame_clen
            sect.frame_maxhandle = frame_maxhandle
            frame_raw = payload[off:off + frame_clen]
            off += frame_clen
            if frame_uclen == frame_clen:
                sect.frame_data = frame_raw
            else:
                sect.frame_data = zlib.decompress(frame_raw)
            sect.vc_maxhandle, used4 = read_varint64(payload, off); off += used4
            sect.vc_start = off  # position of pack_type byte
            if off >= len(payload):
                raise FstFormatError("truncated VCDATA before pack type")
            sect.pack_type = chr(payload[off])
            off += 1
            sect.times = self._parse_time_table(payload)
            self._parse_chain_table(sect, payload)
            self._vc_sections.append(sect)

    def _parse_time_table(self, payload: bytes) -> list[int]:
        n = len(payload)
        if n < 24:
            raise FstFormatError("truncated VCDATA time section")
        tsec_uclen = _u64be(payload, n - 24)
        tsec_clen = _u64be(payload, n - 16)
        tsec_nitems = _u64be(payload, n - 8)
        tsec_start = n - 24 - tsec_clen
        if tsec_start < 0:
            raise FstFormatError("invalid VCDATA time section offset")
        compressed = payload[tsec_start:tsec_start + tsec_clen]
        if tsec_uclen == tsec_clen:
            ucdata = compressed
        else:
            ucdata = zlib.decompress(compressed)
        times: list[int] = []
        tpval = 0
        off = 0
        for _ in range(tsec_nitems):
            val, used = read_varint64(ucdata, off)
            tpval += val
            times.append(tpval)
            off += used
        return times

    def _parse_chain_table(self, sect: VcSection, payload: bytes) -> None:
        n = len(payload)
        tsec_clen = _u64be(payload, n - 16)
        indx_pntr = n - 24 - tsec_clen - 8
        if indx_pntr < 0:
            raise FstFormatError("invalid chain table position")
        chain_clen = _u64be(payload, indx_pntr)
        indx_pos = indx_pntr - chain_clen
        if indx_pos < 0:
            raise FstFormatError("invalid chain table offset")
        chain_data = payload[indx_pos:indx_pos + chain_clen]
        sect.indx_pos = indx_pos
        sect.indx_len = chain_clen
        vc_maxhandle = sect.vc_maxhandle
        chain_table: list[int] = [0] * (vc_maxhandle + 2)
        chain_table_lengths: list[int] = [0] * (vc_maxhandle + 2)
        pnt = 0
        idx = 0
        pval = 0
        pidx = 0
        if sect.block_type == FST_BL_VCDATA_DYN_ALIAS2:
            prev_alias = 0
            while pnt < chain_clen:
                if chain_data[pnt] & 0x01:
                    shval, skiplen = read_svarint64(chain_data, pnt)
                    shval >>= 1
                    if shval > 0:
                        pval += shval
                        chain_table[idx] = pval
                        if idx:
                            chain_table_lengths[pidx] = pval - chain_table[pidx]
                        pidx = idx
                        idx += 1
                    elif shval < 0:
                        chain_table[idx] = 0
                        chain_table_lengths[idx] = shval
                        prev_alias = shval
                        idx += 1
                    else:
                        chain_table[idx] = 0
                        chain_table_lengths[idx] = prev_alias
                        idx += 1
                else:
                    val, skiplen = read_varint32(chain_data, pnt)
                    loopcnt = val >> 1
                    for _ in range(loopcnt):
                        chain_table[idx] = 0
                        idx += 1
                pnt += skiplen
        else:
            while pnt < chain_clen:
                val, skiplen = read_varint32(chain_data, pnt)
                if not val:
                    pnt += skiplen
                    val, skiplen = read_varint32(chain_data, pnt)
                    chain_table[idx] = 0
                    chain_table_lengths[idx] = -val
                    idx += 1
                elif val & 1:
                    pval += (val >> 1)
                    chain_table[idx] = pval
                    if idx:
                        chain_table_lengths[pidx] = pval - chain_table[pidx]
                    pidx = idx
                    idx += 1
                else:
                    loopcnt = val >> 1
                    for _ in range(loopcnt):
                        chain_table[idx] = 0
                        idx += 1
                pnt += skiplen
        chain_table[idx] = indx_pos - sect.vc_start
        chain_table_lengths[pidx] = chain_table[idx] - chain_table[pidx]
        for i in range(idx):
            v = chain_table_lengths[i]
            if v < 0 and chain_table[i] == 0:
                v = -v
                v -= 1
                if v < i:
                    chain_table[i] = chain_table[v]
                    chain_table_lengths[i] = chain_table_lengths[v]
        sect.chain_table = chain_table[:idx]
        sect.chain_table_lengths = chain_table_lengths[:idx]

    @property
    def num_handles(self) -> int:
        return self.header.max_handle

    @property
    def signal_lengths(self) -> list[int]:
        return self._signal_lengths

    @property
    def signal_types(self) -> list[int]:
        return self._signal_types

    def vars(self) -> list[FstVar]:
        return [e for e in self._hierarchy_events if isinstance(e, FstVar)]

    @property
    def handle_to_var(self) -> dict[int, 'FstVar']:
        """Map signal handle (1-indexed) to FstVar."""
        return self._handle_to_var

    def scopes(self) -> list[FstScope]:
        return [e for e in self._hierarchy_events if isinstance(e, FstScope)]

    def hierarchy(self) -> list:
        return list(self._hierarchy_events)

    @property
    def vc_sections(self) -> list[VcSection]:
        return self._vc_sections

    def get_initial_value(self, handle: int, section_index: int = 0) -> bytes:
        if section_index >= len(self._vc_sections):
            raise IndexError(f"section_index {section_index} out of range")
        sect = self._vc_sections[section_index]
        idx = handle - 1
        if idx < 0 or idx >= len(self._signal_lengths):
            raise IndexError(f"handle {handle} out of range")
        off = sum(self._signal_lengths[:idx])
        sig_len = self._signal_lengths[idx]
        return sect.frame_data[off:off + sig_len]

    def iter_value_changes(
        self, handle: int, section_index: int = 0,
    ) -> Iterator[tuple[int, bytes]]:
        if section_index >= len(self._vc_sections):
            return
        sect = self._vc_sections[section_index]
        idx = handle - 1

        if idx >= len(sect.chain_table) or idx >= len(sect.chain_table_lengths):
            initial = self.get_initial_value(handle, section_index)
            yield (sect.beg_time, initial)
            return

        chain_off = sect.chain_table[idx]
        chain_len = sect.chain_table_lengths[idx]

        # Negative chain_len: dynamic alias, return only the initial value
        if chain_len < 0:
            yield (sect.beg_time, self.get_initial_value(handle, section_index))
            return

        if chain_off <= 0 or chain_len <= 0:
            yield (sect.beg_time, self.get_initial_value(handle, section_index))
            return
        payload = self._data
        vc_data_start = sect.block_offset + 9 + sect.vc_start
        vc_data = payload[vc_data_start + chain_off:vc_data_start + chain_off + chain_len]
        sig_len = self._signal_lengths[idx]
        times = sect.times

        # First varint: compressed size (0 = uncompressed)
        comp_size, cskip = read_varint(vc_data, 0)
        if comp_size:
            # Compressed data follows
            from .compression import decompress_block
            comp_body = vc_data[cskip:cskip + chain_len]
            vc_data = decompress_block(comp_body, sect.pack_type, comp_size)
        else:
            # Uncompressed: skip the marker
            vc_data = vc_data[cskip:]
        off = 0
        n = len(vc_data)
        tidx = 0
        while off < n:
            vli, skiplen = read_varint(vc_data, off)
            off += skiplen
            if sig_len <= 1:
                if sig_len == 0:
                    break
                if not (vli & 1):
                    shamt = 2 << (vli & 1)
                    tidx += vli >> shamt
                    val_byte = ((vli >> 1) & 1) | 0x30
                else:
                    shamt = 2 << (vli & 1)
                    tidx += vli >> shamt
                    val_byte = FST_RCV_STR[((vli >> 1) & 7)]
                val = bytes([val_byte])
            else:
                tidx += vli >> 1
                if not (vli & 1):
                    byte_len = (sig_len + 7) // 8
                    raw = bytearray(sig_len)
                    for j in range(sig_len):
                        bp = j // 8
                        bit = 7 - (j & 7)
                        ch = ((vc_data[off + bp] >> bit) & 1) | 0x30
                        raw[j] = ch
                    val = bytes(raw)
                    off += byte_len
                else:
                    val = vc_data[off:off + sig_len]
                    off += sig_len
            if tidx >= len(times):
                break
            yield (times[tidx], val)

    def iter_time_value_pairs(
        self, section_index: int = 0,
    ) -> Iterator[tuple[int, list[tuple[int, bytes]]]]:
        if section_index >= len(self._vc_sections):
            return
        sect = self._vc_sections[section_index]
        times = sect.times
        max_handle = self.header.max_handle
        sig_lens = self._signal_lengths
        sig_typs = self._signal_types
        while len(sig_lens) < max_handle:
            sig_lens.append(1)
        while len(sig_typs) < max_handle:
            sig_typs.append(16)
        tc_head: list[int] = [0] * len(times)
        scatterptr: list[int] = [0] * max_handle
        headptr: list[int] = [0] * max_handle
        length_remaining: list[int] = [0] * max_handle
        traversal_buf = bytearray()
        for idx in range(max_handle):
            if idx >= len(sect.chain_table):
                continue
            chain_off = sect.chain_table[idx]
            chain_len = sect.chain_table_lengths[idx]
            if chain_off < 0 or chain_len <= 0:
                continue
            vc_data_start = sect.block_offset + 9 + sect.vc_start
            start = vc_data_start + chain_off
            raw_compressed = self._data[start:start + chain_len]
            try:
                first_val, skiplen = read_varint32(raw_compressed, 0)
            except FstFormatError:
                continue
            dest_len = first_val
            if first_val:
                comp_data = raw_compressed[skiplen:skiplen + chain_len - skiplen]
                from .compression import decompress_block
                try:
                    decompressed = decompress_block(comp_data, sect.pack_type, dest_len)
                except Exception:
                    continue
            else:
                dest_len = chain_len - skiplen
                decompressed = raw_compressed[skiplen:skiplen + dest_len]
            hptr = len(traversal_buf)
            traversal_buf.extend(decompressed)
            headptr[idx] = hptr
            length_remaining[idx] = dest_len
            vli = peek_varint32(traversal_buf, hptr)
            if sig_lens[idx] == 1:
                shcnt = 2 << (vli & 1)
                tdelta = vli >> shcnt
            else:
                tdelta = vli >> 1
            scatterptr[idx] = tc_head[tdelta]
            tc_head[tdelta] = idx + 1
        initial_vals: list[tuple[int, bytes]] = []
        frame_off = 0
        for idx in range(max_handle):
            sl = sig_lens[idx]
            initial_vals.append((idx + 1, sect.frame_data[frame_off:frame_off + sl]))
            frame_off += sl
        if sect.beg_time != times[0]:
            yield (sect.beg_time, initial_vals)
        for ti in range(len(times)):
            changes: list[tuple[int, bytes]] = []
            while tc_head[ti]:
                idx = tc_head[ti] - 1
                vli, skiplen = read_varint32(traversal_buf, headptr[idx])
                sig_len = sig_lens[idx]
                if sig_len <= 1:
                    if sig_len == 0:
                        headptr[idx] += skiplen
                        length_remaining[idx] -= skiplen
                        tc_head[ti] = scatterptr[idx]
                        scatterptr[idx] = 0
                        continue
                    if not (vli & 1):
                        val_byte = ((vli >> 1) & 1) | 0x30
                    else:
                        val_byte = FST_RCV_STR[((vli >> 1) & 7)]
                    val = bytes([val_byte])
                    headptr[idx] += skiplen
                    length_remaining[idx] -= skiplen
                    tc_head[ti] = scatterptr[idx]
                    scatterptr[idx] = 0
                    if length_remaining[idx]:
                        nv = peek_varint32(traversal_buf, headptr[idx])
                        if sig_len == 1:
                            shamt = 2 << (nv & 1)
                            tdelta = nv >> shamt
                        else:
                            tdelta = nv >> 1
                        next_ti = ti + tdelta
                        if next_ti < len(times):
                            scatterptr[idx] = tc_head[next_ti]
                            tc_head[next_ti] = idx + 1
                else:
                    if not (vli & 1):
                        byte_len = (sig_len + 7) // 8
                        raw = bytearray(sig_len)
                        for j in range(sig_len):
                            bp = j // 8
                            bit = 7 - (j & 7)
                            ch = ((traversal_buf[headptr[idx] + skiplen + bp] >> bit) & 1) | 0x30
                            raw[j] = ch
                        val = bytes(raw)
                        consume = byte_len
                    else:
                        val = bytes(traversal_buf[headptr[idx] + skiplen:headptr[idx] + skiplen + sig_len])
                        consume = sig_len
                    headptr[idx] += skiplen + consume
                    length_remaining[idx] -= skiplen + consume
                    tc_head[ti] = scatterptr[idx]
                    scatterptr[idx] = 0
                    if length_remaining[idx]:
                        nv = peek_varint32(traversal_buf, headptr[idx])
                        tdelta = nv >> 1
                        next_ti = ti + tdelta
                        if next_ti < len(times):
                            scatterptr[idx] = tc_head[next_ti]
                            tc_head[next_ti] = idx + 1
                changes.append((idx + 1, val))
            if changes:
                yield (times[ti], changes)

    def summary(self) -> dict:
        return {
            "path": str(self.path),
            "header": self.header,
            "blocks": [(b.offset, b.block_type, b.section_length) for b in self._blocks],
            "scope_count_parsed": len(self.scopes()),
            "var_count_parsed": len(self.vars()),
            "vc_section_count": len(self._vc_sections),
            "signal_lengths": self._signal_lengths,
        }


def _u64be(buf: bytes, off: int = 0) -> int:
    return int.from_bytes(buf[off:off + 8], "big")


def _i8(byte: int) -> int:
    return byte - 256 if byte >= 128 else byte


def _read_cstr(buf: bytes | bytearray | memoryview, off: int) -> tuple[str, int]:
    end = off
    n = len(buf)
    while end < n and buf[end] != 0:
        end += 1
    if end >= n:
        raise FstFormatError("unterminated C string")
    return bytes(buf[off:end]).decode("utf-8", errors="replace"), end + 1

