"""
Pure-Python FST waveform reader.

Implemented:
  Block types: HDR, VCDATA (static + DYN_ALIAS + DYN_ALIAS2),
  GEOM (zlib), HIER (gzip/LZ4/LZ4DUO), ZWRAPPER, BLACKOUT (raw decode).
  Hierarchy: scope/variable/upscope/attr_begin/attr_end.
  VCDATA: frame + time table + chain index + per-handle value-change
  iteration.  Pack types: zlib, LZ4, FastLZ.
  Signal types: 1-bit, N-bit (packed binary or ASCII), string (varlen).

Not implemented:
  Parallel .hier file support.

Notes:
  Blackout sections are decoded and can be applied by event iterators with
  respect_blackout=True.  SystemVerilog/VHDL helper metadata is decoded from
  hierarchy attributes and attached to variables where libfst writer emits
  it as pre-variable attributes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
import struct
import zlib
import base64

from .common import (
    FstBlockType, FstHeader, FstScope, FstVar, FstUpscope,
    FstAttrBegin, FstAttrEnd, FstFormatError, FstBlock, FstSignalMetadata,
    FST_BL_HDR, FST_BL_VCDATA, FST_BL_BLACKOUT, FST_BL_GEOM,
    FST_BL_HIER, FST_BL_VCDATA_DYN_ALIAS, FST_BL_HIER_LZ4,
    FST_BL_HIER_LZ4DUO, FST_BL_VCDATA_DYN_ALIAS2, FST_BL_ZWRAPPER, FST_BL_SKIP,
    FST_ST_GEN_ATTRBEGIN, FST_ST_GEN_ATTREND,
    FST_ST_VCD_SCOPE, FST_ST_VCD_UPSCOPE, FST_VT_MAX,
    FST_HDR_SIM_VERSION_SIZE, FST_HDR_DATE_SIZE, FST_DOUBLE_ENDTEST,
    FST_RCV_STR, FstVarType, FstAttrType, FstMiscType,
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
    REAL_VAR_TYPES = {
        FstVarType.VCD_REAL,
        FstVarType.VCD_REAL_PARAMETER,
        FstVarType.VCD_REALTIME,
        FstVarType.SV_SHORTREAL,
    }

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
        self._vars_by_handle: dict[int, list['FstVar']] = {}
        self._comments: list[str] = []
        self._env_vars: list[str] = []
        self._value_lists: list[str] = []
        self._enum_tables: dict[int, dict] = {}
        self._source_paths: dict[int, str] = {}
        self._attribute_events: list[FstAttrBegin] = []
        self._attributes_by_handle: dict[int, tuple[FstAttrBegin, ...]] = {}
        self._parse_geometry_and_hierarchy()
        self._build_handle_map()
        self._parse_vc_sections()
        self._parse_blackouts()

    @staticmethod
    def _scan_blocks(data: bytes) -> list[FstBlock]:
        """Scan top-level FST blocks.

        libfst treats FST_BL_SKIP as an end marker.  Some files contain only a
        single trailing 0xff byte, so do not require a full 9-byte section
        header once the marker is seen.
        """
        blocks: list[FstBlock] = []
        off = 0
        n = len(data)
        while off < n:
            block_type = data[off]
            if block_type == FST_BL_SKIP:
                break
            if off + 9 > n:
                raise FstFormatError(f"truncated block header at offset {off}")
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
        timezero = struct.unpack(">q", b[off:off + 8])[0] if off + 8 <= len(b) else 0
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
        self._has_geometry = bool(geom_blocks)
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
        """Parse hierarchy stream and attach common libfst metadata.

        libfst exposes ATTRBEGIN/ATTREND as hierarchy events.  Writer helper
        APIs such as fstWriterCreateVar2(), fstWriterSetValueList(),
        fstWriterEmitEnumTableRef(), and source-stem helpers emit MISC
        attributes immediately before the variable to which they apply.  This
        parser preserves the raw hierarchy events and also attaches those
        helper attributes to the next FstVar as FstSignalMetadata.
        """
        events: list = []
        scopes: list[str] = []
        cur_scope = ""
        current_handle = 0
        off = 0
        n = len(data)
        active_attrs: list[FstAttrBegin] = []
        pending_misc: list[FstAttrBegin] = []
        pending_metadata = FstSignalMetadata()

        def add_pending_misc(attr: FstAttrBegin) -> None:
            nonlocal pending_metadata
            if attr.attr_type != int(FstAttrType.MISC):
                return
            subtype = int(attr.subtype)
            if subtype == int(FstMiscType.COMMENT):
                self._comments.append(attr.name)
            elif subtype == int(FstMiscType.ENVVAR):
                self._env_vars.append(attr.name)
            elif subtype == int(FstMiscType.PATHNAME):
                self._source_paths[int(attr.arg)] = attr.name
            elif subtype == int(FstMiscType.VALUELIST):
                self._value_lists.append(attr.name)
                pending_misc.append(attr)
                pending_metadata = _metadata_replace(pending_metadata, value_list=attr.name)
            elif subtype == int(FstMiscType.SUPVAR):
                pending_misc.append(attr)
                svt = int(attr.arg) >> 10
                sdt = int(attr.arg) & 0x3FF
                pending_metadata = _metadata_replace(
                    pending_metadata,
                    type_name=attr.name,
                    supplemental_var_type=svt,
                    supplemental_data_type=sdt,
                )
            elif subtype == int(FstMiscType.ENUMTABLE):
                if attr.name:
                    self._enum_tables[int(attr.arg)] = _parse_enum_table_attr(attr.name)
                else:
                    pending_misc.append(attr)
                    pending_metadata = _metadata_replace(
                        pending_metadata, enum_table_handle=int(attr.arg)
                    )
            elif subtype in (int(FstMiscType.SOURCESTEM), int(FstMiscType.SOURCEISTEM)):
                pending_misc.append(attr)
                sidx = attr.arg_from_name or _parse_varint_from_attr_name(attr.name)
                stem = (self._source_paths.get(sidx, ""), int(attr.arg))
                if subtype == int(FstMiscType.SOURCESTEM):
                    pending_metadata = _metadata_replace(pending_metadata, source_stem=stem)
                else:
                    pending_metadata = _metadata_replace(
                        pending_metadata, source_instantiation_stem=stem
                    )
            else:
                # Preserve third-party/vendor MISC attrs and attach them to the
                # next variable.  No semantic interpretation is attempted; the
                # raw payload is exposed through describe_attribute().
                pending_misc.append(attr)

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
                if off + 2 > n:
                    raise FstFormatError("truncated attrbegin")
                attr_type = data[off]; subtype = data[off + 1]; off += 2
                name_raw, off = _read_cstr_raw(data, off)
                arg, used = read_varint(data, off); off += used
                arg_from_name = 0
                if attr_type == int(FstAttrType.MISC) and subtype in (
                    int(FstMiscType.SOURCESTEM), int(FstMiscType.SOURCEISTEM)
                ):
                    try:
                        arg_from_name, _ = read_varint(name_raw, 0)
                    except Exception:
                        arg_from_name = 0
                name = _decode_attr_name(name_raw, attr_type, subtype)
                attr = FstAttrBegin(attr_type, subtype, name, arg, arg_from_name, name_raw)
                events.append(attr)
                self._attribute_events.append(attr)
                if attr_type == int(FstAttrType.MISC):
                    add_pending_misc(attr)
                else:
                    active_attrs.append(attr)
            elif tag == FST_ST_GEN_ATTREND:
                events.append(FstAttrEnd())
                if active_attrs:
                    active_attrs.pop()
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
                active_tuple = tuple(active_attrs)
                misc_tuple = tuple(pending_misc)
                metadata = _metadata_replace(
                    pending_metadata,
                    active_attributes=active_tuple,
                    misc_attributes=misc_tuple,
                    array_attributes=tuple(
                        a for a in active_tuple if a.attr_type == int(FstAttrType.ARRAY)
                    ),
                    enum_attributes=tuple(
                        a for a in active_tuple if a.attr_type == int(FstAttrType.ENUM)
                    ),
                    pack_attributes=tuple(
                        a for a in active_tuple if a.attr_type == int(FstAttrType.PACK)
                    ),
                    all_attributes=active_tuple + misc_tuple,
                )
                self._attributes_by_handle[handle] = metadata.all_attributes
                events.append(FstVar(
                    tag, direction, name, length, handle, is_alias, full,
                    metadata.supplemental_var_type,
                    metadata.supplemental_data_type,
                    metadata.type_name,
                    metadata,
                ))
                pending_misc = []
                pending_metadata = FstSignalMetadata()
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
        self._patch_signal_info_from_hierarchy()
        self._build_frame_prefix()

    def _patch_signal_info_from_hierarchy(self) -> None:
        """Fill or refine signal length/type arrays from hierarchy records.

        GEOM is authoritative for frame sizes when present, but it deliberately
        collapses most non-real types to "wire".  The hierarchy stream carries
        the real var_type, and older or utility-generated FSTs may omit GEOM.
        Mirror libfst's fallback: derive canonical handle lengths/types from
        hierarchy whenever GEOM is missing or incomplete, and use hierarchy to
        refine signal_types without changing GEOM-derived sizes.
        """
        max_handle = int(self.header.max_handle)
        if len(self._signal_lengths) < max_handle:
            self._signal_lengths.extend([1] * (max_handle - len(self._signal_lengths)))
        if len(self._signal_types) < max_handle:
            self._signal_types.extend([int(FstVarType.VCD_WIRE)] * (max_handle - len(self._signal_types)))

        for e in self._hierarchy_events:
            if not isinstance(e, FstVar) or e.is_alias:
                continue
            idx = e.handle - 1
            if idx < 0:
                continue
            while idx >= len(self._signal_lengths):
                self._signal_lengths.append(1)
                self._signal_types.append(int(FstVarType.VCD_WIRE))
            vt = int(e.var_type)
            self._signal_types[idx] = int(FstVarType.VCD_REAL) if vt in self.REAL_VAR_TYPES else vt
            # If GEOM was absent, derive the frame width from HIER.  If GEOM
            # exists, keep its frame sizes because they are the layout source
            # for VCDATA frame_data.
            if not getattr(self, "_has_geometry", False):
                if vt in self.REAL_VAR_TYPES:
                    self._signal_lengths[idx] = 8
                elif vt == int(FstVarType.GEN_STRING):
                    self._signal_lengths[idx] = 0
                else:
                    self._signal_lengths[idx] = int(e.length)

    def _build_frame_prefix(self) -> None:
        # Precompute frame data prefix offsets for O(1) get_initial_value.
        self._frame_prefix: list[int] = [0]
        for sl in self._signal_lengths:
            self._frame_prefix.append(self._frame_prefix[-1] + max(0, int(sl)))

    def _build_handle_map(self) -> None:
        """Build handle->FstVar lookup dict.

        The first (non-alias) var for each handle is canonical.
        Subsequent aliases are stored in _vars_by_handle.
        """
        for e in self._hierarchy_events:
            if isinstance(e, FstVar):
                if e.handle not in self._handle_to_var:
                    self._handle_to_var[e.handle] = e
                self._vars_by_handle.setdefault(e.handle, []).append(e)

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
        # Build O(1) lookup for cumulative time indices
        self._time_to_index: dict[int, int] = {t: i for i, t in enumerate(times)}
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

    def _parse_blackouts(self) -> None:
        self._blackouts: list[tuple[int, bool]] = []
        for b in self._blocks:
            if b.block_type == FST_BL_BLACKOUT:
                p = b.payload
                if len(p) < 2:
                    continue
                num, used = read_varint(p, 0)
                off = used
                cur_time = 0
                for _ in range(num):
                    if off >= len(p):
                        break
                    active = p[off] != 0
                    off += 1
                    delta, used2 = read_varint64(p, off)
                    off += used2
                    cur_time += delta
                    self._blackouts.append((cur_time, active))

    @property
    def blackouts(self) -> list[tuple[int, bool]]:
        """Blackout transitions as ``(time, is_dump_active)``.

        This mirrors libfst's ``blackout_times`` / ``blackout_activity``
        arrays.  Event iterators keep raw VCDATA behavior by default; pass
        ``respect_blackout=True`` to suppress events while dump is inactive.
        """
        return list(self._blackouts)

    def is_dump_active_at(self, time: int) -> bool:
        """Return dump-active state after applying blackout transitions <= time."""
        active = True
        for t, state in self._blackouts:
            if t > time:
                break
            active = bool(state)
        return active

    def iter_blackout_intervals(
        self, start: int | None = None, end: int | None = None,
    ) -> Iterator[tuple[int, int | None, bool]]:
        """Yield dump-active intervals as ``(begin, end, active)``.

        ``end`` is exclusive and may be ``None`` for the final open interval.
        ``start``/``end`` trim the yielded intervals but do not mutate the
        underlying blackout transitions.
        """
        lo = self.header.start_time if start is None else int(start)
        hi = self.header.end_time if end is None else int(end)
        points = [(lo, self.is_dump_active_at(lo))]
        points.extend((t, a) for t, a in self._blackouts if lo < t < hi)
        for i, (t, active) in enumerate(points):
            nt = points[i + 1][0] if i + 1 < len(points) else hi
            if nt > t:
                yield (t, nt, active)

    @property
    def comments(self) -> list[str]:
        return list(self._comments)

    @property
    def env_vars(self) -> list[str]:
        return list(self._env_vars)

    @property
    def value_lists(self) -> list[str]:
        return list(self._value_lists)

    @property
    def enum_tables(self) -> dict[int, dict]:
        return dict(self._enum_tables)

    @property
    def source_paths(self) -> dict[int, str]:
        return dict(self._source_paths)

    def attributes(self, *, decoded: bool = False) -> list:
        """Return all parsed FST hierarchy attributes.

        ``decoded=False`` returns the raw ``FstAttrBegin`` records.
        ``decoded=True`` returns dictionaries with category/subtype names and
        decoded payloads where applicable.
        """
        if not decoded:
            return list(self._attribute_events)
        return [self.describe_attribute(a) for a in self._attribute_events]

    def attributes_for_handle(self, handle: int, *, decoded: bool = False) -> list:
        """Return attributes attached to a handle's hierarchy variable.

        This includes currently active ARRAY/ENUM/PACK attributes plus MISC
        helper attributes immediately preceding that variable.
        """
        attrs = list(self._attributes_by_handle.get(handle, ()))
        if not decoded:
            return attrs
        return [self.describe_attribute(a) for a in attrs]

    def describe_attribute(self, attr: FstAttrBegin) -> dict:
        """Decode one FST hierarchy attribute into a structured dictionary.

        Tool-specific/unknown payloads are not semantically guessed.  They are
        nevertheless reported losslessly through the ``payload`` field, which
        contains safe ASCII/escaped/hex/base64 views of the raw hierarchy
        attribute name/payload bytes.
        """
        return _describe_attribute(attr, self._source_paths, self._enum_tables)

    def attribute_payload(self, attr: FstAttrBegin) -> dict:
        """Return safe textual views of one attribute's raw payload bytes."""
        return _attribute_payload_report(attr)

    def attribute_report(self, *, decoded: bool = True) -> list[dict]:
        """Return a report-friendly list of all hierarchy attributes.

        ``decoded=True`` includes category/subtype names plus payload readouts.
        ``decoded=False`` returns a compact raw numeric report while still
        including the escaped payload text.
        """
        if decoded:
            return [self.describe_attribute(a) for a in self._attribute_events]
        return [
            {
                "attr_type": int(a.attr_type),
                "subtype": int(a.subtype),
                "arg": int(a.arg),
                "arg_from_name": int(a.arg_from_name),
                "payload": _attribute_payload_report(a),
            }
            for a in self._attribute_events
        ]

    def attribute_report_text(self) -> str:
        """Return a human-readable text report of all hierarchy attrs."""
        lines: list[str] = []
        for idx, attr in enumerate(self._attribute_events):
            desc = self.describe_attribute(attr)
            payload = desc["payload"]
            lines.append(
                f"[{idx}] {desc['attr_type_name']}/{desc['subtype_name']} "
                f"arg={desc['arg']} payload={payload['ascii_escaped']}"
            )
            if payload["hex"]:
                lines.append(f"    hex={payload['hex']}")
        return "\n".join(lines)

    def iter_vcd_extension_lines(self) -> Iterator[str]:
        """Yield libfst-style VCD extension lines for hierarchy attributes.

        This is not a full FST-to-VCD exporter.  It is a lossless textual view
        of the ATTRBEGIN/ATTREND/comment metadata already present in the FST
        hierarchy stream, following the formatting used by libfst when
        ``use_vcd_extensions`` is enabled.
        """
        for event in self._hierarchy_events:
            if isinstance(event, FstAttrBegin):
                yield from _format_attr_as_vcd_extension(event)
            elif isinstance(event, FstAttrEnd):
                yield "$attrend $end"

    def metadata_for_handle(self, handle: int) -> FstSignalMetadata | None:
        var = self._handle_to_var.get(handle)
        return var.metadata if var is not None else None

    @property
    def num_handles(self) -> int:
        return self.header.max_handle

    @property
    def signal_lengths(self) -> list[int]:
        return self._signal_lengths

    @property
    def signal_types(self) -> list[int]:
        return self._signal_types

    def is_string_handle(self, handle: int) -> bool:
        idx = handle - 1
        if idx < 0 or idx >= len(self._signal_lengths):
            return False
        return self._signal_lengths[idx] == 0

    def is_real_handle(self, handle: int) -> bool:
        idx = handle - 1
        if idx < 0 or idx >= len(self._signal_types):
            return False
        return self._signal_types[idx] == int(FstVarType.VCD_REAL)

    def decode_value(self, handle: int, value: bytes):
        """Decode a raw FST value into a convenient Python value.

        Fixed-width scalar/vector values are returned as ASCII strings.
        GEN_STRING values remain bytes so binary payloads are preserved.
        Real values are returned as Python float using the file's double
        endian marker, matching libfst's callback conversion mode.
        """
        if self.is_real_handle(handle):
            if len(value) < 8:
                raise FstFormatError(f"real value for handle {handle} is shorter than 8 bytes")
            fmt = "<d" if self.header.double_endian_match else ">d"
            return struct.unpack(fmt, value[:8])[0]
        if self.is_string_handle(handle):
            return bytes(value)
        return bytes(value).decode("ascii", errors="replace")

    def get_initial_value_decoded(self, handle: int, section_index: int = 0):
        return self.decode_value(handle, self.get_initial_value(handle, section_index))

    def iter_decoded_value_changes(
        self, handle: int, section_index: int = 0,
    ) -> Iterator[tuple[int, object]]:
        for t, v in self.iter_value_changes(handle, section_index):
            yield t, self.decode_value(handle, v)

    def iter_value_changes_all(
        self, handle: int, *, include_initial: bool = False, respect_blackout: bool = False,
    ) -> Iterator[tuple[int, bytes]]:
        """Iterate a handle's value changes across all VCDATA sections.

        include_initial=True emits each section's frame value before that
        section's explicit changes.  This is useful for waveform slicing,
        where a time-window boundary needs a correct starting snapshot.
        """
        for section_index, sect in enumerate(self._vc_sections):
            if include_initial and (not respect_blackout or self.is_dump_active_at(sect.beg_time)):
                yield sect.beg_time, self.get_initial_value(handle, section_index)
            yield from self.iter_value_changes(handle, section_index, respect_blackout=respect_blackout)

    def iter_decoded_value_changes_all(
        self, handle: int, *, include_initial: bool = False, respect_blackout: bool = False,
    ) -> Iterator[tuple[int, object]]:
        for t, v in self.iter_value_changes_all(handle, include_initial=include_initial, respect_blackout=respect_blackout):
            yield t, self.decode_value(handle, v)

    def vars(self) -> list[FstVar]:
        return [e for e in self._hierarchy_events if isinstance(e, FstVar)]

    @property
    def handle_to_var(self) -> dict[int, 'FstVar']:
        """Map signal handle (1-indexed) to canonical FstVar."""
        return self._handle_to_var

    def vars_by_handle(self, handle: int) -> list['FstVar']:
        """Return all FstVar entries (canonical + aliases) for a handle."""
        return self._vars_by_handle.get(handle, [])

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
        off = self._frame_prefix[idx]
        sig_len = self._signal_lengths[idx]
        return sect.frame_data[off:off + sig_len]

    def iter_value_changes(
        self, handle: int, section_index: int = 0, *, respect_blackout: bool = False,
    ) -> Iterator[tuple[int, bytes]]:
        if section_index >= len(self._vc_sections):
            return
        sect = self._vc_sections[section_index]
        idx = handle - 1

        if idx >= len(sect.chain_table) or idx >= len(sect.chain_table_lengths):
            initial = self.get_initial_value(handle, section_index)
            if not respect_blackout or self.is_dump_active_at(sect.beg_time):
                yield (sect.beg_time, initial)
            return

        chain_off = sect.chain_table[idx]
        chain_len = sect.chain_table_lengths[idx]

        # Negative chain_len: dynamic alias, return only the initial value
        if chain_len < 0:
            if not respect_blackout or self.is_dump_active_at(sect.beg_time):
                yield (sect.beg_time, self.get_initial_value(handle, section_index))
            return

        if chain_off <= 0 or chain_len <= 0:
            if idx < len(self._signal_lengths) and not self._signal_lengths[idx]:
                return  # string with no data: emit nothing (C reader behavior)
            if not respect_blackout or self.is_dump_active_at(sect.beg_time):
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
            if sig_len == 0:
                # variable-length string: (tdelta, length, bytes)
                if vli & 1:
                    break  # unknown encoding
                tidx += vli >> 1
                length, lskip = read_varint(vc_data, off)
                off += lskip
                val = bytes(vc_data[off:off + length])
                off += length
                if tidx >= len(times):
                    break
                if not respect_blackout or self.is_dump_active_at(times[tidx]):
                    yield (times[tidx], val)
                continue
            if sig_len <= 1:
                # Single-bit: value encoded in vli
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
            if not respect_blackout or self.is_dump_active_at(times[tidx]):
                yield (times[tidx], val)

    def iter_time_value_pairs(
        self, section_index: int = 0, *, respect_blackout: bool = False,
    ) -> Iterator[tuple[int, list[tuple[int, bytes]]]]:
        """Yield time-ordered changes for one VCDATA section.

        Empty frame-only sections are valid FST and yield the section snapshot
        at beg_time.  Dynamic-alias chain-table entries are already resolved in
        _parse_chain_table, matching libfst's chain reuse behavior.
        """
        if section_index >= len(self._vc_sections):
            return
        sect = self._vc_sections[section_index]
        times = sect.times or []
        max_handle = self.header.max_handle
        sig_lens = list(self._signal_lengths)
        sig_typs = list(self._signal_types)
        while len(sig_lens) < max_handle:
            sig_lens.append(1)
        while len(sig_typs) < max_handle:
            sig_typs.append(int(FstVarType.VCD_WIRE))

        initial_vals: list[tuple[int, bytes]] = []
        frame_off = 0
        for idx in range(max_handle):
            sl = max(0, sig_lens[idx])
            initial_vals.append((idx + 1, sect.frame_data[frame_off:frame_off + sl]))
            frame_off += sl

        if not times:
            if initial_vals and (not respect_blackout or self.is_dump_active_at(sect.beg_time)):
                yield (sect.beg_time, initial_vals)
            return

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
                comp_data = raw_compressed[skiplen:]
                from .compression import decompress_block
                decompressed = decompress_block(comp_data, sect.pack_type, dest_len)
            else:
                dest_len = chain_len - skiplen
                decompressed = raw_compressed[skiplen:skiplen + dest_len]
            if not decompressed:
                continue
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
            if tdelta < len(times):
                scatterptr[idx] = tc_head[tdelta]
                tc_head[tdelta] = idx + 1

        if sect.beg_time != times[0] and (not respect_blackout or self.is_dump_active_at(sect.beg_time)):
            yield (sect.beg_time, initial_vals)
        for ti in range(len(times)):
            changes: list[tuple[int, bytes]] = []
            while tc_head[ti]:
                idx = tc_head[ti] - 1
                vli, skiplen = read_varint32(traversal_buf, headptr[idx])
                sig_len = sig_lens[idx]
                if sig_len <= 1:
                    if sig_len == 0:
                        # variable-length string: (tdelta, length, bytes)
                        if not (vli & 1):
                            strlen, lskip2 = read_varint32(traversal_buf, headptr[idx] + skiplen)
                            raw_val = bytes(traversal_buf[headptr[idx] + skiplen + lskip2:headptr[idx] + skiplen + lskip2 + strlen])
                            val = raw_val
                            consume = skiplen + lskip2 + strlen
                            headptr[idx] += consume
                            length_remaining[idx] -= consume
                            tc_head[ti] = scatterptr[idx]
                            scatterptr[idx] = 0
                            if length_remaining[idx] > 0:
                                nv = peek_varint32(traversal_buf, headptr[idx])
                                tdelta = nv >> 1
                                next_ti = ti + tdelta
                                if next_ti < len(times):
                                    scatterptr[idx] = tc_head[next_ti]
                                    tc_head[next_ti] = idx + 1
                            changes.append((idx + 1, val))
                        else:
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
                    if length_remaining[idx] > 0:
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
                    if length_remaining[idx] > 0:
                        nv = peek_varint32(traversal_buf, headptr[idx])
                        tdelta = nv >> 1
                        next_ti = ti + tdelta
                        if next_ti < len(times):
                            scatterptr[idx] = tc_head[next_ti]
                            tc_head[next_ti] = idx + 1
                changes.append((idx + 1, val))
            if changes and (not respect_blackout or self.is_dump_active_at(times[ti])):
                yield (times[ti], changes)

    def iter_time_value_pairs_all(self, *, respect_blackout: bool = False) -> Iterator[tuple[int, list[tuple[int, bytes]]]]:
        """Yield time/value batches from all VCDATA sections in file order."""
        for idx in range(len(self._vc_sections)):
            yield from self.iter_time_value_pairs(idx, respect_blackout=respect_blackout)

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

def _read_cstr_raw(buf: bytes | bytearray | memoryview, off: int) -> tuple[bytes, int]:
    end = off
    n = len(buf)
    while end < n and buf[end] != 0:
        end += 1
    if end >= n:
        raise FstFormatError("unterminated C string")
    return bytes(buf[off:end]), end + 1


def _decode_attr_name(raw: bytes, attr_type: int, subtype: int) -> str:
    # SOURCESTEM/SOURCEISTEM overload the name field with varint bytes.  Keep
    # those bytes reversible via latin-1; normal textual attributes are UTF-8.
    if attr_type == int(FstAttrType.MISC) and subtype in (
        int(FstMiscType.SOURCESTEM), int(FstMiscType.SOURCEISTEM)
    ):
        return raw.decode("latin1", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _parse_varint_from_attr_name(name: str) -> int:
    if not name:
        return 0
    try:
        val, _ = read_varint(name.encode("latin1"), 0)
        return int(val)
    except Exception:
        return 0




_ATTR_TYPE_NAMES = {
    int(FstAttrType.MISC): "misc",
    int(FstAttrType.ARRAY): "array",
    int(FstAttrType.ENUM): "enum",
    int(FstAttrType.PACK): "pack",
}
_MISC_SUBTYPE_NAMES = {
    int(FstMiscType.COMMENT): "comment",
    int(FstMiscType.ENVVAR): "envvar",
    int(FstMiscType.SUPVAR): "supvar",
    int(FstMiscType.PATHNAME): "pathname",
    int(FstMiscType.SOURCESTEM): "sourcestem",
    int(FstMiscType.SOURCEISTEM): "sourceistem",
    int(FstMiscType.VALUELIST): "valuelist",
    int(FstMiscType.ENUMTABLE): "enumtable",
    int(FstMiscType.UNKNOWN): "unknown",
}
_ARRAY_SUBTYPE_NAMES = {0: "none", 1: "unpacked", 2: "packed", 3: "sparse"}
_ENUM_SUBTYPE_NAMES = {
    0: "sv_integer",
    1: "sv_bit",
    2: "sv_logic",
    3: "sv_int",
    4: "sv_shortint",
    5: "sv_longint",
    6: "sv_byte",
    7: "sv_unsigned_integer",
    8: "sv_unsigned_bit",
    9: "sv_unsigned_logic",
    10: "sv_unsigned_int",
    11: "sv_unsigned_shortint",
    12: "sv_unsigned_longint",
    13: "sv_unsigned_byte",
    14: "reg",
    15: "time",
}
_PACK_SUBTYPE_NAMES = {0: "none", 1: "unpacked", 2: "packed", 3: "tagged_packed"}


def _attribute_subtype_name(attr_type: int, subtype: int) -> str:
    if attr_type == int(FstAttrType.MISC):
        return _MISC_SUBTYPE_NAMES.get(subtype, f"misc_{subtype}")
    if attr_type == int(FstAttrType.ARRAY):
        return _ARRAY_SUBTYPE_NAMES.get(subtype, f"array_{subtype}")
    if attr_type == int(FstAttrType.ENUM):
        return _ENUM_SUBTYPE_NAMES.get(subtype, f"enum_{subtype}")
    if attr_type == int(FstAttrType.PACK):
        return _PACK_SUBTYPE_NAMES.get(subtype, f"pack_{subtype}")
    return str(subtype)


def _fst_unescape(text: str) -> str:
    """Decode libfst enum-table escape sequences (fstUtilityEscToBin)."""
    out = bytearray()
    b = text.encode("latin1", errors="replace")
    i = 0
    while i < len(b):
        ch = b[i]
        if ch != 0x5C:  # backslash
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= len(b):
            out.append(0x5C)
            break
        esc = chr(b[i])
        i += 1
        mapping = {
            "a": 7,
            "b": 8,
            "f": 12,
            "n": 10,
            "r": 13,
            "t": 9,
            "v": 11,
            "'": ord("'"),
            '"': ord('"'),
            "\\": ord("\\"),
            "?": ord("?"),
        }
        if esc in mapping:
            out.append(mapping[esc])
        elif esc == "x" and i + 1 < len(b):
            try:
                out.append(int(bytes(b[i:i+2]).decode("ascii"), 16))
                i += 2
            except ValueError:
                out.append(ord("x"))
        elif esc in "01234567" and i + 1 < len(b):
            octal = bytes([ord(esc)]) + b[i:i+2]
            try:
                out.append(int(octal.decode("ascii"), 8))
                i += 2
            except ValueError:
                out.append(ord(esc))
        else:
            out.append(ord(esc))
    return out.decode("utf-8", errors="replace")



def _attr_payload_bytes(attr: FstAttrBegin) -> bytes:
    raw = getattr(attr, "name_raw", b"")
    if raw:
        return bytes(raw)
    # Compatibility for FstAttrBegin instances constructed by older tests/users.
    return str(attr.name).encode("utf-8", errors="replace")


def _is_printable_ascii_byte(b: int) -> bool:
    return 0x20 <= b <= 0x7E


def _escape_bytes_for_report(raw: bytes) -> str:
    """Return a reversible, report-friendly C-style escaped byte string."""
    out: list[str] = []
    for b in raw:
        if b == 0x5C:  # backslash
            out.append(r"\\")
        elif b == 0x0A:
            out.append(r"\n")
        elif b == 0x0D:
            out.append(r"\r")
        elif b == 0x09:
            out.append(r"\t")
        elif b == 0x00:
            out.append(r"\0")
        elif _is_printable_ascii_byte(b):
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02x}")
    return "".join(out)


def _attribute_payload_report(attr: FstAttrBegin) -> dict:
    raw = _attr_payload_bytes(attr)
    printable = all(_is_printable_ascii_byte(b) or b in (0x09, 0x0A, 0x0D) for b in raw)
    return {
        "length": len(raw),
        "ascii_escaped": _escape_bytes_for_report(raw),
        "utf8": raw.decode("utf-8", errors="replace"),
        "latin1": raw.decode("latin1", errors="replace"),
        "hex": raw.hex(),
        "base64": base64.b64encode(raw).decode("ascii"),
        "is_printable_ascii": bool(printable),
    }


def _describe_attribute(attr: FstAttrBegin, source_paths: dict[int, str], enum_tables: dict[int, dict]) -> dict:
    attr_type = int(attr.attr_type)
    subtype = int(attr.subtype)
    payload = _attribute_payload_report(attr)
    d = {
        "attr_type": attr_type,
        "attr_type_name": _ATTR_TYPE_NAMES.get(attr_type, f"attr_{attr_type}"),
        "subtype": subtype,
        "subtype_name": _attribute_subtype_name(attr_type, subtype),
        "name": attr.name,
        "arg": int(attr.arg),
        "arg_from_name": int(attr.arg_from_name),
        "payload": payload,
        "payload_ascii": payload["ascii_escaped"],
    }
    if attr_type == int(FstAttrType.MISC):
        if subtype == int(FstMiscType.SUPVAR):
            d["type_name"] = attr.name
            d["supplemental_var_type"] = int(attr.arg) >> 10
            d["supplemental_data_type"] = int(attr.arg) & 0x3FF
        elif subtype in (int(FstMiscType.SOURCESTEM), int(FstMiscType.SOURCEISTEM)):
            sidx = attr.arg_from_name or _parse_varint_from_attr_name(attr.name)
            d["source_index"] = int(sidx)
            d["path"] = source_paths.get(int(sidx), "")
            d["line"] = int(attr.arg)
        elif subtype == int(FstMiscType.ENUMTABLE):
            if attr.name:
                d["enum_table"] = _parse_enum_table_attr(attr.name)
            else:
                d["enum_table_handle"] = int(attr.arg)
                if int(attr.arg) in enum_tables:
                    d["enum_table"] = enum_tables[int(attr.arg)]
        elif subtype == int(FstMiscType.VALUELIST):
            d["value_list"] = attr.name
        elif subtype == int(FstMiscType.PATHNAME):
            d["source_index"] = int(attr.arg)
            d["path"] = attr.name
    elif attr_type == int(FstAttrType.ARRAY):
        d["array_kind"] = d["subtype_name"]
        d["element_count"] = int(attr.arg)
    elif attr_type == int(FstAttrType.ENUM):
        d["enum_value_type"] = d["subtype_name"]
        d["element_count"] = int(attr.arg)
    elif attr_type == int(FstAttrType.PACK):
        d["pack_kind"] = d["subtype_name"]
        d["member_count"] = int(attr.arg)
    return d

def _parse_enum_table_attr(text: str) -> dict:
    # Writer encodes: name count literals... values... .  This mirrors
    # fstUtilityExtractEnumTableFromString(): split by spaces, then apply
    # fstUtilityEscToBin to literal/value tokens.  The raw tokens are retained
    # because third-party writers may use noncanonical escaping.
    parts = text.split()
    if len(parts) < 2:
        return {
            "raw": text,
            "name": text,
            "count": 0,
            "literals": [],
            "values": [],
            "raw_literals": [],
            "raw_values": [],
        }
    name = parts[0]
    try:
        count = int(parts[1])
    except ValueError:
        count = 0
    raw_literals = parts[2:2 + count]
    raw_values = parts[2 + count:2 + 2 * count]
    literals = [_fst_unescape(x) for x in raw_literals]
    values = [_fst_unescape(x) for x in raw_values]
    return {
        "raw": text,
        "name": name,
        "count": count,
        "literals": literals,
        "values": values,
        "raw_literals": raw_literals,
        "raw_values": raw_values,
    }




def _quote_empty_attr_name(name: str) -> str:
    return name if name else '""'


def _format_attr_as_vcd_extension(attr: FstAttrBegin) -> Iterator[str]:
    """Format one attribute like libfst's VCD extension printer."""
    attr_type = int(attr.attr_type)
    subtype = int(attr.subtype)
    attr_name = _ATTR_TYPE_NAMES.get(attr_type, "misc")
    name = _quote_empty_attr_name(attr.name)
    if attr_type == int(FstAttrType.ARRAY):
        yield f"$attrbegin {attr_name} {_ARRAY_SUBTYPE_NAMES.get(subtype, 'none')} {name} {int(attr.arg)} $end"
    elif attr_type == int(FstAttrType.ENUM):
        yield f"$attrbegin {attr_name} {_ENUM_SUBTYPE_NAMES.get(subtype, 'sv_integer')} {name} {int(attr.arg)} $end"
    elif attr_type == int(FstAttrType.PACK):
        yield f"$attrbegin {attr_name} {_PACK_SUBTYPE_NAMES.get(subtype, 'none')} {name} {int(attr.arg)} $end"
    else:
        if subtype == int(FstMiscType.COMMENT):
            yield "$comment"
            yield f"\t{attr.name}"
            yield "$end"
        elif subtype in (int(FstMiscType.SOURCESTEM), int(FstMiscType.SOURCEISTEM)):
            sidx = attr.arg_from_name or _parse_varint_from_attr_name(attr.name)
            yield f"$attrbegin misc {subtype:02x} {int(sidx)} {int(attr.arg)} $end"
        else:
            yield f"$attrbegin misc {subtype:02x} {name} {int(attr.arg)} $end"

def _metadata_replace(meta: FstSignalMetadata, **kwargs) -> FstSignalMetadata:
    data = {
        "type_name": meta.type_name,
        "supplemental_var_type": meta.supplemental_var_type,
        "supplemental_data_type": meta.supplemental_data_type,
        "value_list": meta.value_list,
        "enum_table_handle": meta.enum_table_handle,
        "source_stem": meta.source_stem,
        "source_instantiation_stem": meta.source_instantiation_stem,
        "active_attributes": meta.active_attributes,
        "misc_attributes": meta.misc_attributes,
        "array_attributes": meta.array_attributes,
        "enum_attributes": meta.enum_attributes,
        "pack_attributes": meta.pack_attributes,
        "all_attributes": meta.all_attributes,
    }
    data.update(kwargs)
    return FstSignalMetadata(**data)

