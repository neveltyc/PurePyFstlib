"""Reader-side regression checks for compatibility edge cases."""

from __future__ import annotations

import math
import struct
import tempfile
from pathlib import Path

from truepyfstlib import FstReader, FstWriter
from truepyfstlib.common import FST_BL_GEOM, FstScopeType, FstVarDir, FstVarType


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


def main() -> None:
    tests = [
        test_reader_derives_geometry_from_hierarchy,
        test_reader_empty_section_iter_time_value_pairs,
        test_reader_decodes_real_values,
        test_reader_all_section_iteration,
    ]
    for t in tests:
        t()
        print(f"OK   {t.__name__}")
    print(f"All {len(tests)} reader tests passed.")


if __name__ == "__main__":
    main()
