"""Validate writer roundtrip: write a scenario, verify fst2vcd output.

These tests exercise writer edge cases that the golden fixtures may miss
(e.g., sparse writes, string signals, large handle counts).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from truepyfstlib import FstWriter, FstReader
from truepyfstlib.common import FstScopeType, FstVarType, FstVarDir, FstAttrType


def _fst2vcd_ok(path: str) -> str:
    """Run fst2vcd and return stdout."""
    r = subprocess.run(["fst2vcd", "-f", path], capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        raise RuntimeError(f"fst2vcd failed: {r.stderr.strip()}")
    return r.stdout


def test_minimal():
    """1-bit toggle."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "sig")
    w.set_upscope()
    w.emit_time_change(0); w.emit_value_change(1, b"1")
    w.emit_time_change(5); w.emit_value_change(1, b"0")
    w.close()
    vcd = _fst2vcd_ok(path)
    assert "1!" in vcd and "0!" in vcd
    r = FstReader(path)
    changes = list(r.iter_value_changes(1))
    assert len(changes) == 2, f"got {changes}"
    Path(path).unlink()


def test_sparse():
    """Some signals never emitted."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "a")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "b")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "c")
    w.set_upscope()
    w.emit_time_change(0); w.emit_value_change(1, b"1")
    w.close()
    _fst2vcd_ok(path)  # just ensure no crash
    r = FstReader(path)
    assert len(list(r.iter_value_changes(2))) == 1  # frame initial only
    Path(path).unlink()


def test_string_var():
    """String signal roundtrip."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    w.create_var(FstVarType.GEN_STRING, FstVarDir.IMPLICIT, 0, "msg", is_string=True)
    w.set_upscope()
    w.emit_time_change(0); w.emit_value_change(1, b"hello")
    w.emit_time_change(10); w.emit_value_change(1, b"world")
    w.close()
    vcd = _fst2vcd_ok(path)
    assert "shello" in vcd and "sworld" in vcd
    r = FstReader(path)
    assert list(r.iter_value_changes(1)) == [(0, b"hello"), (10, b"world")]
    Path(path).unlink()


def test_alias():
    """Alias signal: multiple names, one handle."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "clk")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "clk2", alias_handle=h)
    w.set_upscope()
    w.emit_time_change(0); w.emit_value_change(h, b"1")
    w.close()
    r = FstReader(path)
    assert r.header.var_count == 2
    assert r.header.max_handle == 1
    assert r.handle_to_var[1].name == "clk"
    assert len(r.vars_by_handle(1)) == 2
    Path(path).unlink()


def test_handle_validation():
    """Invalid handles raise KeyError."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "sig")
    w.set_upscope()
    try:
        w.emit_value_change(999, b"0")
        assert False, "should have raised"
    except KeyError:
        pass
    Path(path).unlink()


def test_alias_does_not_overwrite_canonical():
    """Alias must not change geometry width of canonical handle."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 8, "a")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 8, "a_alias", alias_handle=h)
    w.set_upscope()
    w.emit_time_change(0)
    w.emit_value_change(h, b"10101010")
    w.close()
    r = FstReader(path)
    assert r.header.max_handle == 1
    assert r.header.var_count == 2
    assert r.signal_lengths[0] == 8
    changes = list(r.iter_value_changes(h))
    assert len(changes) == 1 and changes[0][1] == b"10101010"
    Path(path).unlink()


def test_alias_unknown_handle_rejected():
    """Alias to non-existent handle must raise KeyError."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    try:
        w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "bad", alias_handle=999)
        assert False, "should have raised"
    except KeyError:
        pass
    Path(path).unlink()


def test_multi_section_state_inherited():
    """Second section initial frame inherits last value from first section."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "s")
    w.set_upscope()
    # Section 1: s=0 at t=10 (initial 0, no change at t=0)
    w.emit_time_change(10)
    w.emit_value_change(h, b"1")
    w.flush_context()
    # Section 2: starts with s=1 (inherited from sec1 end), changes to 0 at t=20
    w.emit_time_change(20)
    w.emit_value_change(h, b"0")
    w.close()
    r = FstReader(path)
    assert len(r.vc_sections) == 2
    assert list(r.iter_value_changes(h, 0)) == [(10, b"1")]
    assert list(r.iter_value_changes(h, 1)) == [(20, b"0")]
    # Verify section 1 initial frame = b"0" (section began at start_time=0 with s=0)
    sec1_frame = r.get_initial_value(h, 0)
    assert sec1_frame == b"0", f"sec1 initial frame expected b'0', got {sec1_frame!r}"
    # Verify section 2 initial frame = b"1" (inherited from sec1 end)
    sec2_frame = r.get_initial_value(h, 1)
    assert sec2_frame == b"1", f"sec2 initial frame expected b'1', got {sec2_frame!r}"
    Path(path).unlink()


def test_flush_refuses_backwards_time():
    """After flush, time must not go backwards."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "s")
    w.set_upscope()
    w.emit_time_change(100)
    w.emit_value_change_bit(h, 1)
    w.flush_context()
    try:
        w.emit_time_change(50)
        assert False, "should have raised"
    except ValueError:
        pass
    Path(path).unlink()


def test_emit_bit_rejects_invalid():
    """emit_value_change_bit must reject non-0/1 values."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "s")
    w.set_upscope()
    for bad in [-1, 2, 3]:
        try:
            w.emit_value_change_bit(h, bad)
            assert False, f"should have raised for {bad}"
        except ValueError:
            pass
    Path(path).unlink()


def test_gen_string_auto_detect():
    """FstVarType.GEN_STRING sets is_string=True automatically."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.GEN_STRING, FstVarDir.IMPLICIT, 0, "msg")
    w.set_upscope()
    w.emit_time_change(0)
    w.emit_value_change(h, b"hello")
    w.close()
    r = FstReader(path)
    assert list(r.iter_value_changes(h)) == [(0, b"hello")]
    Path(path).unlink()


def test_attr_rejects_negative_type():
    """set_attr_begin must reject negative attr_type or subtype."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    try:
        w.set_attr_begin(-1, 0, "bad", 0)
        assert False, "should have raised"
    except ValueError:
        pass
    try:
        w.set_attr_begin(0, -1, "bad", 0)
        assert False, "should have raised"
    except ValueError:
        pass
    Path(path).unlink()


def test_empty_file_has_vc_section():
    """File with variables but no value changes must have an actual VCDATA section."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "s")
    w.set_upscope()
    w.close()
    r = FstReader(path)
    assert r.header.value_change_section_count == 1
    assert len(r.vc_sections) == 1, f"expected 1 VCDATA section, got {len(r.vc_sections)}"
    Path(path).unlink()


def test_blackout_reader_exposes_intervals():
    """Reader should expose blackout intervals from writer."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "s")
    w.set_upscope()
    w.emit_dump_active(False)
    w.emit_time_change(100)
    w.emit_dump_active(True)
    w.emit_time_change(100)
    w.emit_value_change(h, b"1")
    w.close()
    r = FstReader(path)
    assert len(r.blackouts) == 2
    assert r.blackouts[0] == (0, False)
    assert r.blackouts[1] == (100, True)
    Path(path).unlink()


def test_zero_length_non_string_rejected():
    """Non-string variables with length=0 must raise ValueError."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    try:
        w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 0, "bad")
        assert False, "should have raised"
    except ValueError:
        pass
    Path(path).unlink()


def test_emit_value_change_accepts_str():
    """emit_value_change for string vars should accept Python str."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.GEN_STRING, FstVarDir.IMPLICIT, 0, "msg")
    w.set_upscope()
    w.emit_time_change(0)
    w.emit_value_change(h, "hello")  # str, not bytes
    w.close()
    r = FstReader(path)
    assert list(r.iter_value_changes(h)) == [(0, b"hello")]
    Path(path).unlink()
def test_empty_file_accepted_by_fst2vcd():
    """Empty file with variables must pass fst2vcd acceptance."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "s")
    w.set_upscope()
    w.close()
    vcd = _fst2vcd_ok(path)
    assert "$var" in vcd
    Path(path).unlink()


def test_xz_values_roundtrip():
    """All x/z/h/u/w/l/-/? values roundtrip correctly."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "sig")
    w.set_upscope()
    expected = []
    t = 0
    for ch in b"xzhuwl-?":
        w.emit_time_change(t)
        w.emit_value_change(h, bytes([ch]))
        expected.append((t, bytes([ch])))
        t += 5
    w.close()
    r = FstReader(path)
    assert list(r.iter_value_changes(h)) == expected
    Path(path).unlink()


def test_nbit_vector_boundaries():
    """Multi-bit vectors of various widths roundtrip correctly."""
    for width in [2, 8, 33, 64]:
        with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
            path = f.name
        w = FstWriter(path, timescale=-9)
        w.set_scope(FstScopeType.VCD_MODULE, "top")
        h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, width, "vec")
        w.set_upscope()
        val_a = b"0" * width
        val_b = b"1" * width
        if width == 33:
            val_b = b"1" * 33
        w.emit_time_change(0)
        w.emit_value_change(h, val_a)
        w.emit_time_change(10)
        w.emit_value_change(h, val_b)
        w.emit_time_change(20)
        w.emit_value_change(h, val_a)
        w.close()
        r = FstReader(path)
        changes = list(r.iter_value_changes(h))
        assert changes == [(0, val_a), (10, val_b), (20, val_a)], f"width={width}: {changes}"
        _fst2vcd_ok(path)
        Path(path).unlink()


def test_xz_in_multibit():
    """Multi-bit vectors with x/z values roundtrip."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 8, "bus")
    w.set_upscope()
    w.emit_time_change(0)
    w.emit_value_change(h, b"xxxxxxxx")
    w.emit_time_change(10)
    w.emit_value_change(h, b"zzzzzzzz")
    w.close()
    r = FstReader(path)
    assert list(r.iter_value_changes(h)) == [(0, b"xxxxxxxx"), (10, b"zzzzzzzz")]
    _fst2vcd_ok(path)
    Path(path).unlink()


def test_multisection_nbit_string_mixed():
    """Multi-section with N-bit vector + string inheriting across sections."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h1 = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 8, "data")
    h2 = w.create_var(FstVarType.GEN_STRING, FstVarDir.IMPLICIT, 0, "log")
    w.set_upscope()
    # Section 1
    w.emit_time_change(10)
    w.emit_value_change(h1, b"10101010")
    w.emit_value_change(h2, b"start")
    w.flush_context()
    # Section 2: inherits data=b"10101010", log=b"start"
    w.emit_time_change(20)
    w.emit_value_change(h1, b"11110000")
    w.emit_time_change(30)
    w.emit_value_change(h2, b"end")
    w.close()
    r = FstReader(path)
    assert len(r.vc_sections) == 2
    assert r.get_initial_value(h1, 0) == b"00000000"  # sec1 initial
    assert r.get_initial_value(h1, 1) == b"10101010"  # sec2 initial inherited
    assert list(r.iter_value_changes(h2, 0)) == [(10, b"start")]
    assert list(r.iter_value_changes(h2, 1)) == [(30, b"end")]
    _fst2vcd_ok(path)
    Path(path).unlink()


def test_hierarchy_frozen_after_first_emit():
    """create_var must raise after emit_time_change is called."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "a")
    w.set_upscope()
    w.emit_time_change(0)
    w.emit_value_change(1, b"1")
    try:
        w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "b")
        assert False, "should have raised"
    except RuntimeError:
        pass
    Path(path).unlink()


def test_alias_mismatch_rejected():
    """Alias with mismatched type/length must raise ValueError."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 4, "data")
    w.set_upscope()
    try:
        w.create_var(FstVarType.VCD_REG, FstVarDir.IMPLICIT, 4, "data2", alias_handle=h)
        assert False, "should have raised for type mismatch"
    except ValueError:
        pass
    try:
        w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 8, "data2", alias_handle=h)
        assert False, "should have raised for length mismatch"
    except ValueError:
        pass
    Path(path).unlink()


def test_close_then_mutate_rejected():
    """emit_value_change after close must raise RuntimeError."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name
    w = FstWriter(path, timescale=-9)
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    h = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "a")
    w.set_upscope()
    w.close()
    try:
        w.emit_value_change(h, b"1")
        assert False, "should have raised"
    except RuntimeError:
        pass
    Path(path).unlink()



def main():
    tests = [
        test_minimal, test_sparse, test_string_var, test_alias,
        test_handle_validation,
        test_alias_does_not_overwrite_canonical,
        test_alias_unknown_handle_rejected,
        test_multi_section_state_inherited,
        test_flush_refuses_backwards_time,
        test_emit_bit_rejects_invalid,
        test_gen_string_auto_detect,
        test_attr_rejects_negative_type,
        test_empty_file_has_vc_section,
        test_blackout_reader_exposes_intervals,
        test_zero_length_non_string_rejected,
        test_emit_value_change_accepts_str,
        test_empty_file_accepted_by_fst2vcd,
        test_xz_values_roundtrip,
        test_nbit_vector_boundaries,
        test_xz_in_multibit,
        test_multisection_nbit_string_mixed,
        test_hierarchy_frozen_after_first_emit,
        test_alias_mismatch_rejected,
        test_close_then_mutate_rejected,
    ]
    for t in tests:
        t()
        print(f"OK   {t.__name__}")
    print(f"All {len(tests)} writer tests passed.")


if __name__ == "__main__":
    main()

