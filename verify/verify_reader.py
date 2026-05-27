"""Reader-side regression checks for compatibility edge cases."""

from __future__ import annotations

import math
import struct
import json
import tempfile
from pathlib import Path

from truepyfstlib import FstReader, FstWriter
from truepyfstlib.common import (
    FST_BL_GEOM, FstScopeType, FstVarDir, FstVarType,
    FstAttrType, FstArrayType, FstMiscType,
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
    try:
        r.close()
    except (NameError, OSError):
        pass
    p.unlink(missing_ok=True)
    try:
        r.close()
    except (NameError, OSError):
        pass
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
    try:
        r.close()
    except (NameError, OSError):
        pass
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
    try:
        r.close()
    except (NameError, OSError):
        pass
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
    try:
        r.close()
    except (NameError, OSError):
        pass
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
    try:
        r.close()
    except (NameError, OSError):
        pass
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
    assert len(meta.array_attributes) == 1
    assert meta.array_attributes[0].subtype == int(FstArrayType.PACKED)
    assert r.attributes_for_handle(h) == list(meta.all_attributes)
    decoded = r.attributes_for_handle(h, decoded=True)
    assert any(d.get("array_kind") == "packed" and d.get("element_count") == 4 for d in decoded)
    assert any(d.get("type_name") == "std_logic" for d in decoded)
    attr_lines = list(r.iter_vcd_extension_lines())
    assert "$comment" in attr_lines
    assert any(line.startswith("$attrbegin array packed packed 4") for line in attr_lines)
    assert any("$attrbegin misc 04" in line and "42" in line for line in attr_lines)
    all_decoded = r.attributes(decoded=True)
    assert any(d["subtype_name"] == "enumtable" and d.get("enum_table", {}).get("name") == "state_t" for d in all_decoded)
    var = r.handle_to_var[h]
    assert var.supplemental_type_name == "std_logic"
    assert var.supplemental_var_type == 1
    assert var.supplemental_data_type == 6
    try:
        r.close()
    except (NameError, OSError):
        pass
    p.unlink(missing_ok=True)


def test_reader_reports_unknown_attr_payload_as_text() -> None:
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as tf:
        p = Path(tf.name)
    w = FstWriter(p, start_time=0)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    # Third-party/vendor attributes may use printable text mixed with opaque
    # non-ASCII bytes.  Reader should not guess semantics, but must preserve the
    # original C-string bytes and expose report-safe textual forms.
    payload = "vendor\x01\x7fÿ"
    w.set_attr_begin(FstAttrType.MISC, FstMiscType.UNKNOWN, payload, 123)
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "s")
    w.set_attr_end()
    w.set_upscope()
    w.close()

    r = FstReader(str(p))
    attrs = r.attributes_for_handle(h)
    assert len(attrs) == 1
    raw = attrs[0].name_raw
    assert raw.startswith(b"vendor\x01\x7f")
    decoded = r.attributes_for_handle(h, decoded=True)[0]
    payload_report = decoded["payload"]
    assert payload_report["length"] == len(raw)
    assert "vendor" in payload_report["ascii_escaped"]
    assert "\\x01" in payload_report["ascii_escaped"]
    assert "\\x7f" in payload_report["ascii_escaped"]
    assert payload_report["hex"] == raw.hex()
    assert payload_report["base64"]
    assert decoded["payload_ascii"] == payload_report["ascii_escaped"]
    report = r.attribute_report_text()
    assert "vendor" in report and "hex=" in report
    try:
        r.close()
    except (NameError, OSError):
        pass
    p.unlink(missing_ok=True)


def test_reader_mmap_context_manager_and_no_read_bytes_copy() -> None:
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as tf:
        p = Path(tf.name)
    w = FstWriter(p, start_time=0)
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "s")
    w.emit_time_change(0)
    w.emit_value_change(h, b"1")
    w.close()

    with FstReader(str(p)) as r:
        assert r.header.max_handle == 1
        assert list(r.iter_value_changes(h)) == [(0, b"1")]
        # Normal, non-ZWRAPPER files should be mmap-backed instead of copied
        # into one giant bytes object.
        assert getattr(r, "_mmap", None) is not None

    r2 = FstReader(str(p), use_mmap=False)
    assert list(r2.iter_value_changes(h)) == [(0, b"1")]
    r2.close()
    try:
        r.close()
    except (NameError, OSError):
        pass
    p.unlink(missing_ok=True)


def test_reader_random_access_signal_and_time_window() -> None:
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as tf:
        p = Path(tf.name)
    w = FstWriter(p, start_time=0)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    w.set_scope(FstScopeType.VCD_MODULE, "u0")
    h_state = w.create_var(FstVarType.VCD_REG, FstVarDir.IMPLICIT, 4, "state")
    h_flag = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "flag")
    w.set_upscope()
    w.set_scope(FstScopeType.VCD_MODULE, "u1")
    h_data = w.create_var(FstVarType.VCD_REG, FstVarDir.IMPLICIT, 8, "data")
    w.set_upscope()
    w.set_upscope()

    w.emit_time_change(0)
    w.emit_value_change(h_state, b"0000")
    w.emit_value_change_bit(h_flag, 0)
    w.emit_value_change(h_data, b"00000000")
    w.emit_time_change(10)
    w.emit_value_change(h_state, b"0001")
    w.flush_context()
    w.emit_time_change(20)
    w.emit_value_change(h_state, b"0010")
    w.emit_time_change(30)
    w.emit_value_change_bit(h_flag, 1)
    w.emit_value_change(h_data, b"11110000")
    w.close()

    r = FstReader(str(p))
    assert r.find_handle("top.u0.state") == h_state
    assert set(r.find_handles("top.u0.*")) == {h_state, h_flag}
    assert r.names_for_handle(h_state) == ["top.u0.state"]
    assert r.sections_overlapping(15, 25) == [1]
    assert r.section_for_time(15) == 1

    assert r.get_value_at(h_state, 5) == b"0000"
    assert r.get_value_at(h_state, 15) == b"0001"
    assert r.get_value_at(h_state, 25) == b"0010"
    assert r.get_value_at("top.u0.state", 25, decoded=True) == "0010"

    assert list(r.iter_value_changes_range(h_state, 15, 25, include_initial=True)) == [
        (15, b"0001"),
        (20, b"0010"),
    ]
    assert list(r.iter_value_changes_range("top.u0.state", 20, 25, include_initial=True)) == [
        (20, b"0010"),
    ]
    # Range iteration without include_initial should not leak synthetic section
    # snapshots for signals that did not change inside the requested window.
    assert list(r.iter_value_changes_range(h_flag, 15, 25)) == []
    grouped = list(r.iter_selected_changes(
        ["top.u0.state", "top.u0.flag"], 15, 25, include_initial=True, decoded=True
    ))
    assert grouped[0] == (15, [(h_state, "0001"), (h_flag, "0")])
    assert grouped[1] == (20, [(h_state, "0010")])
    try:
        r.close()
    except (NameError, OSError):
        pass
    p.unlink(missing_ok=True)


def test_reader_stable_info_and_integration_api() -> None:
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as tf:
        p = Path(tf.name)
    w = FstWriter(p, start_time=0)
    w.set_version("api-test")
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h_state = w.create_var(FstVarType.VCD_REG, FstVarDir.IMPLICIT, 4, "state")
    h_flag = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "flag")
    w.set_upscope()
    w.emit_time_change(0)
    w.emit_value_change(h_state, b"0000")
    w.emit_value_change_bit(h_flag, 0)
    w.emit_time_change(5)
    w.emit_value_change(h_state, b"0011")
    w.emit_time_change(10)
    w.emit_value_change_bit(h_flag, 1)
    w.close()

    r = FstReader(str(p))
    assert not hasattr(r, "summary")

    info = r.file_info()
    json.dumps(info)
    assert info["file"] == str(p)
    assert info["version"] == "api-test"
    assert info["var_count"] == 2
    assert info["max_handle"] == 2
    assert info["parsed_value_change_section_count"] == 1

    blocks = r.block_table()
    assert blocks and all("block_type_name" in b for b in blocks)
    sections = r.section_table()
    assert len(sections) == 1
    assert sections[0]["begin_time"] == 0
    assert sections[0]["end_time"] == 10

    table = r.signal_table()
    json.dumps(table)
    assert [row["name"] for row in table] == ["top.state", "top.flag"]
    assert r.find_signal("top.state")["handle"] == h_state
    assert {row["handle"] for row in r.find_signals("top.*")} == {h_state, h_flag}
    assert r.resolve_handle("top.state") == h_state
    assert r.get_value_from_handle_at_time(h_state, 7) == b"0011"
    assert r.get_value_from_handle_at_time("top.flag", 7, decoded=True) == "0"
    assert r.section_at_time(7) == r.section_for_time(7) == 0

    assert list(r.iter_events(0, 10, [h_state], decoded=True)) == [
        (0, h_state, "0000"),
        (5, h_state, "0011"),
    ]
    groups = list(r.iter_event_groups(0, 10, [h_state, h_flag], decoded=True))
    assert groups[0] == (0, [(h_state, "0000"), (h_flag, "0")])
    assert groups[-1] == (10, [(h_flag, "1")])
    assert r.snapshot_at(7, [h_state, h_flag], decoded=True) == {
        h_state: "0011",
        h_flag: "0",
    }
    assert r.format_value(h_state, b"0011") == "3 (0x3)"
    assert r.format_value(h_state, b"00xz") == "b00xz"

    assert r.get_version_string() == "api-test"
    assert r.get_var_count() == 2
    assert r.get_scope_count() == 1
    assert r.get_alias_count() == 0
    assert r.get_start_time() == 0
    assert r.get_end_time() == 10
    assert r.get_value_change_section_count() == 1
    assert r.get_max_handle() == 2
    try:
        r.close()
    except (NameError, OSError):
        pass
    p.unlink(missing_ok=True)

def main() -> None:
    tests = [
        test_reader_derives_geometry_from_hierarchy,
        test_reader_empty_section_iter_time_value_pairs,
        test_reader_decodes_real_values,
        test_reader_all_section_iteration,
        test_reader_blackout_semantic_filtering,
        test_reader_attaches_sv_vhdl_metadata,
        test_reader_reports_unknown_attr_payload_as_text,
        test_reader_mmap_context_manager_and_no_read_bytes_copy,
        test_reader_random_access_signal_and_time_window,
        test_reader_stable_info_and_integration_api,
    ]
    for t in tests:
        t()
        print(f"OK   {t.__name__}")
    print(f"All {len(tests)} reader tests passed.")


if __name__ == "__main__":
    main()
