"""Validate writer roundtrip: write a scenario, verify fst2vcd output.

These tests exercise writer edge cases that the golden fixtures may miss
(e.g., sparse writes, string signals, large handle counts).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from truepyfstlib import FstWriter, FstReader
from truepyfstlib.common import FstScopeType, FstVarType, FstVarDir


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


def main():
    tests = [
        test_minimal, test_sparse, test_string_var, test_alias,
        test_handle_validation,
    ]
    for t in tests:
        t()
        print(f"OK   {t.__name__}")
    print(f"All {len(tests)} writer tests passed.")


if __name__ == "__main__":
    main()

