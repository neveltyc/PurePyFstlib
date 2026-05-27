"""Reader-side regression checks for compatibility edge cases."""

from __future__ import annotations

import math
import struct
import tempfile
from pathlib import Path

from truepyfstlib import FstReader, FstWriter
from truepyfstlib.common import (
    FST_BL_GEOM, FstScopeType, FstVarDir, FstVarType,
    FstAttrType, FstArrayType,
)


def _strip_block(path: Path, block_type: int) -> Path:
    data = path.read_bytes()
    out = bytearray()
    off = 0
    n = len(data)
    while off < n:
        bt = data[off]
        if bt == 255:
            break
        if off + 9 > n:
            raise AssertionError("truncated fixture while stripping block")
        seclen = int.from_bytes(data[off + 1:off + 9], "big")
        end = off + 1 + seclen
        if bt != block_type:
            out.extend(data[off:end])
        off = end
    fd = tempfile.NamedTemporaryFile(suffix=".fst", delete=False)
    fd.close()
    out_path = Path(fd.name)
    out_path.write_bytes(out)
    return out_path


def _make_basic(path: Path) -> tuple[int, int, int]:
    w = FstWriter(path, start_time=0)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h1 = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "flag")
    h2 = w.create_var(FstVarType.VCD_REG, FstVarDir.IMPLICIT, 4, "state")
    h3 = w.create_var(FstVarType.GEN_STRING, FstVarDir.IMPLICIT, 0, "msg")
    w.set_upscope()
    w.emit_time_change(0)
    w.emit_value_change_bit(h1, 1)
    w.emit_value_change(h2, b"1010")
    w.emit_variable_length_value_change(h3, "hello")
    w.close()
    return h1, h2, h3


def test_reader_derives_geometry_from_hierarchy() -> None:
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as tf:
        p = Path(tf.name)
    h1, h2, h3 = _make_basic(p)
    stripped = _strip_block(p, FST_BL_GEOM)
    r = FstReader(str(stripped))
    assert r.signal_lengths[h1 - 1] == 1
    assert r.signal_lengths[h2 - 1] == 4
    assert r.signal_lengths[h3 - 1] == 0
    assert list(r.iter_value_changes(h2)) == [(0, b"1010")]
    assert list(r.iter_value_changes(h3)) == [(0, b"hello")]
    p.unlink(missing_ok=True)
    stripped.unlink(missing_ok=True)


def test_reader_empty_section_iter_time_value_pairs() -> None:
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as tf:
        p = Path(tf.name)
    w = FstWriter(p, start_time=5)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 2, "a")
    w.set_upscope()
    w.close()
    r = FstReader(str(p))
    batches = list(r.iter_time_value_pairs(0))
    assert batches == [(5, [(h, b"xx")])]
    p.unlink(missing_ok=True)


def test_reader_decodes_real_values() -> None:
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as tf:
        p = Path(tf.name)
    w = FstWriter(p, start_time=0)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_REAL, FstVarDir.IMPLICIT, 8, "r")
    w.set_upscope()
    w.emit_time_change(10)
    w.emit_value_change_real(h, 3.25)
    w.close()
    r = FstReader(str(p))
    init = r.get_initial_value_decoded(h)
    assert math.isnan(init)
    changes = list(r.iter_decoded_value_changes(h))
    assert changes == [(10, 3.25)]
    p.unlink(missing_ok=True)


def test_reader_all_section_iteration() -> None:
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as tf:
        p = Path(tf.name)
    w = FstWriter(p, start_time=0)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "a")
    w.set_upscope()
    w.emit_time_change(0)
    w.emit_value_change_bit(h, 0)
    w.emit_time_change(10)
    w.emit_value_change_bit(h, 1)
    w.flush_context()
    w.emit_time_change(20)
    w.emit_value_change_bit(h, 0)
    w.close()
    r = FstReader(str(p))
    assert list(r.iter_value_changes_all(h)) == [(0, b"0"), (10, b"1"), (20, b"0")]
    assert list(r.iter_value_changes_all(h, include_initial=True))[0] == (0, b"x")
    p.unlink(missing_ok=True)



def test_reader_blackout_semantic_filtering() -> None:
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as tf:
        p = Path(tf.name)
    w = FstWriter(p, start_time=0)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "a")
    w.set_upscope()
    w.emit_time_change(0)
    w.emit_value_change_bit(h, 0)
    w.emit_time_change(5)
    w.emit_dump_active(False)
    w.emit_time_change(6)
    w.emit_value_change_bit(h, 1)
    w.emit_time_change(10)
    w.emit_dump_active(True)
    w.emit_time_change(12)
    w.emit_value_change_bit(h, 0)
    w.close()
    r = FstReader(str(p))
    assert r.blackouts == [(5, False), (10, True)]
    assert r.is_dump_active_at(4) is True
    assert r.is_dump_active_at(6) is False
    assert r.is_dump_active_at(10) is True
    assert list(r.iter_blackout_intervals(0, 13)) == [(0, 5, True), (5, 10, False), (10, 13, True)]
    assert list(r.iter_value_changes(h)) == [(0, b"0"), (6, b"1"), (12, b"0")]
    assert list(r.iter_value_changes(h, respect_blackout=True)) == [(0, b"0"), (12, b"0")]
    p.unlink(missing_ok=True)


def test_reader_attaches_sv_vhdl_metadata() -> None:
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as tf:
        p = Path(tf.name)
    w = FstWriter(p, start_time=0)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    w.set_comment("reader comment")
    w.set_env_var("A=B")
    w.set_source_stem("src/top.sv", 42, use_realpath=False)
    w.set_source_instantiation_stem("src/inst.sv", 7, use_realpath=False)
    w.set_value_list("0 1 x z")
    enum_h = w.create_enum_table("state_t", ["IDLE", "RUN"], ["00", "01"], min_valbits=2)
    w.emit_enum_table_ref(enum_h)
    w.set_attr_begin(FstAttrType.ARRAY, FstArrayType.PACKED, "packed", 4)
    h = w.create_var2(
        FstVarType.SV_LOGIC, FstVarDir.IMPLICIT, 1, "s",
        type_name="std_logic",
        supplemental_var_type=1,
        supplemental_data_type=6,
    )
    w.set_attr_end()
    w.set_upscope()
    w.close()
    r = FstReader(str(p))
    meta = r.metadata_for_handle(h)
    assert meta is not None
    assert r.comments == ["reader comment"]
    assert r.env_vars == ["A=B"]
    assert "0 1 x z" in r.value_lists
    assert meta.type_name == "std_logic"
    assert meta.supplemental_var_type == 1
    assert meta.supplemental_data_type == 6
    assert meta.value_list == "0 1 x z"
    assert meta.enum_table_handle == enum_h
    assert r.enum_tables[enum_h]["name"] == "state_t"
    assert r.enum_tables[enum_h]["literals"] == ["IDLE", "RUN"]
    assert meta.source_stem == ("src/top.sv", 42)
    assert meta.source_instantiation_stem == ("src/inst.sv", 7)
    assert any(a.attr_type == int(FstAttrType.ARRAY) for a in meta.active_attributes)
    var = r.handle_to_var[h]
    assert var.supplemental_type_name == "std_logic"
    assert var.supplemental_var_type == 1
    assert var.supplemental_data_type == 6
    p.unlink(missing_ok=True)

def main() -> None:
    tests = [
        test_reader_derives_geometry_from_hierarchy,
        test_reader_empty_section_iter_time_value_pairs,
        test_reader_decodes_real_values,
        test_reader_all_section_iteration,
        test_reader_blackout_semantic_filtering,
        test_reader_attaches_sv_vhdl_metadata,
    ]
    for t in tests:
        t()
        print(f"OK   {t.__name__}")
    print(f"All {len(tests)} reader tests passed.")


if __name__ == "__main__":
    main()
