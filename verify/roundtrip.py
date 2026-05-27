"""Roundtrip tests: write FST files and verify they can be read back."""

import tempfile
from pathlib import Path

from truepyfstlib import FstWriter, FstReader
from truepyfstlib.common import FstScopeType, FstVarType, FstVarDir


def test_single_bit_signal():
    """Write a single 1-bit signal with toggles, read back and verify."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name

    w = FstWriter(path, start_time=0, timescale=-9, version="Test")
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "clk")
    w.set_upscope()

    w.emit_time_change(0)
    w.emit_value_change_bit(1, 1)
    w.emit_time_change(5)
    w.emit_value_change_bit(1, 0)
    w.emit_time_change(10)
    w.emit_value_change_bit(1, 1)
    w.close()

    r = FstReader(path)
    changes = list(r.iter_value_changes(1))

    # Frame value at t=0 is b'0', then value change at t=0 sets to b'1'
    assert len(changes) == 3
    assert changes[0] == (0, b"1")
    assert changes[1] == (5, b"0")
    assert changes[2] == (10, b"1")

    Path(path).unlink()


def test_multi_bit_signal():
    """Write a 4-bit signal with multiple values."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name

    w = FstWriter(path, start_time=0, timescale=-9, version="Test")
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 4, "data")
    w.set_upscope()

    w.emit_time_change(0)
    w.emit_value_change(1, b"0000")
    w.emit_time_change(10)
    w.emit_value_change(1, b"0101")
    w.emit_time_change(20)
    w.emit_value_change(1, b"1111")
    w.close()

    r = FstReader(path)
    changes = list(r.iter_value_changes(1))

    assert len(changes) == 3
    assert changes[0] == (0, b"0000")
    assert changes[1] == (10, b"0101")
    assert changes[2] == (20, b"1111")

    Path(path).unlink()


def test_two_signals():
    """Write two signals and verify both are read correctly."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name

    w = FstWriter(path, start_time=0, timescale=-9, version="Test")
    w.set_scope(FstScopeType.VCD_MODULE, "top")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "clk")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 8, "bus")
    w.set_upscope()

    w.emit_time_change(0)
    w.emit_value_change_bit(1, 1)
    w.emit_value_change(2, b"00000000")
    w.emit_time_change(15)
    w.emit_value_change_bit(1, 0)
    w.emit_value_change(2, b"10101010")
    w.close()

    r = FstReader(path)

    sig1 = list(r.iter_value_changes(1))
    assert sig1 == [(0, b"1"), (15, b"0")]

    sig2 = list(r.iter_value_changes(2))
    assert sig2 == [(0, b"00000000"), (15, b"10101010")]

    Path(path).unlink()


def test_header_fields():
    """Verify header fields survive roundtrip."""
    with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as f:
        path = f.name

    w = FstWriter(path, start_time=100, timescale=-12, version="MySim v1.0",
                  date="2025-01-01", filetype=0)
    w.set_scope(FstScopeType.VCD_MODULE, "tb")
    w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "sig")
    w.set_upscope()
    w.emit_time_change(100)
    w.emit_value_change_bit(1, 1)
    w.close()

    r = FstReader(path)
    h = r.header
    assert h.start_time == 100
    assert h.end_time == 100
    assert h.timescale == -12
    assert "MySim" in h.version
    assert h.date == "2025-01-01"

    Path(path).unlink()


if __name__ == "__main__":
    test_single_bit_signal()
    test_multi_bit_signal()
    test_two_signals()
    test_header_fields()
    print("All tests passed!")

