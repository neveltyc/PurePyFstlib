"""Cross-validate PurePyFstlib reader against fst2vcd reference output.

For each FST fixture in verify/fixtures/, runs fst2vcd to get reference
VCD, parses both outputs into (time, signal, value) events, and compares.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FST_FILES = sorted(FIXTURES_DIR.glob("*.fst"))


def parse_vcd_events(vcd_text: str) -> dict[tuple[int, str], str]:
    """Parse VCD into {(time, signal_name): value} mapping (last value wins)."""
    events: dict[tuple[int, str], str] = {}
    current_time = 0
    signal_map: dict[str, str] = {}
    in_body = False

    for line in vcd_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("$var"):
            parts = line.split()
            # Format: $var type width id name $end
            if len(parts) >= 5:
                sig_id = parts[3]
                sig_name = parts[4]
                signal_map[sig_id] = sig_name
        elif line.startswith("$enddefinitions"):
            in_body = True
            continue
        elif line.startswith("$"):
            continue  # skip $dumpvars, $end, etc.
        elif line.startswith("#"):
            current_time = int(line[1:])
        elif in_body:
            # Check all known signal IDs (sorted by length descending
            # so "ab" matches before "a" or "b")
            for sig_id in sorted(signal_map, key=len, reverse=True):
                if line.endswith(sig_id):
                    val = line[:len(line) - len(sig_id)]
                    sig_name = signal_map[sig_id]
                    events[(current_time, sig_name)] = val.strip()
                    break

    return events


def get_reader_events(fst_path: Path) -> dict[tuple[int, str], str]:
    """Extract events from PurePyFstlib reader."""
    from truepyfstlib import FstReader

    r = FstReader(str(fst_path))
    events: dict[tuple[int, str], str] = {}
    for h in range(1, r.header.max_handle + 1):
        if h not in r.handle_to_var:
            continue
        var = r.handle_to_var[h]
        for t, val in r.iter_value_changes(h):
            vstr = val.decode("ascii", errors="replace") if isinstance(val, bytes) else val
            # Use the last (canonical) value at each time
            events[(t, var.full_name)] = vstr
    return events


def _normalize_value(val: str) -> str:
    """Normalize VCD value: strip 'b' prefix and trailing space."""
    val = val.strip()
    if val.startswith("b") and len(val) > 1:
        val = val[1:]
    return val

def _leaf_name(full_name: str) -> str:
    """Extract leaf name, stripping scope prefix and [N:M] bit range."""
    leaf = full_name.rsplit(".", 1)[-1] if "." in full_name else full_name
    # Strip bit range suffix like " [7:0]" or " [31:0]"
    bracket = leaf.rfind(" [")
    if bracket > 0:
        leaf = leaf[:bracket]
    return leaf

def compare_events(fst_path: Path) -> tuple[int, int, list[str]]:
    """Compare reader events against fst2vcd reference."""
    result = subprocess.run(
        ["fst2vcd", "-f", str(fst_path)],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        return 0, 0, [f"fst2vcd failed: {result.stderr.strip()}"]

    ref_events = parse_vcd_events(result.stdout)
    reader_events_raw = get_reader_events(fst_path)

    # Build reader leaf-name index
    leaf_events: dict[tuple[int, str], str] = {}
    for (t, full_name), val in reader_events_raw.items():
        leaf_events[(t, _leaf_name(full_name))] = val

    mismatches = []
    matched = 0

    for (t, name), ref_val in sorted(ref_events.items()):
        ref_norm = _normalize_value(ref_val)
        key = (t, name)
        if key in leaf_events:
            reader_val = leaf_events[key]
            if reader_val == ref_norm:
                matched += 1
            else:
                mismatches.append(
                    f"  t={t} {name}: ref={ref_norm!r} reader={reader_val!r}"
                )
        else:
            mismatches.append(
                f"  t={t} {name}: ref={ref_norm!r} reader=MISSING"
            )

    extra = set(leaf_events.keys()) - set(ref_events.keys())
    for (t, name) in sorted(extra):
        mismatches.append(
            f"  t={t} {name}: reader={leaf_events[(t,name)]!r} ref=MISSING"
        )

    return matched, len(mismatches), mismatches


def _verify_writer_roundtrips() -> list[str]:
    from truepyfstlib import FstWriter, FstReader
    from truepyfstlib.common import FstScopeType, FstVarType, FstVarDir
    import tempfile

    failed = []
    for fst_path in FST_FILES:
        name = fst_path.stem
        try:
            r = FstReader(str(fst_path))
            with tempfile.NamedTemporaryFile(suffix=".fst", delete=False) as tf:
                writer_path = tf.name
            w = FstWriter(writer_path, start_time=r.header.start_time,
                           timescale=r.header.timescale,
                           version="golden-roundtrip")
            # Replay: one scope, one var per handle, emit all changes
            w.set_scope(FstScopeType.VCD_MODULE, "top")
            for h in sorted(r.handle_to_var.keys()):
                vi = r.handle_to_var[h]
                w.create_var(vi.var_type, vi.direction, vi.length, vi.name,
                              is_string=(vi.length == 0))
            w.set_upscope()
            # Emit in time order: group changes by time
            events_by_time: dict[int, list[tuple[int, bytes]]] = {}
            for h in sorted(r.handle_to_var.keys()):
                for t, v in r.iter_value_changes(h):
                    events_by_time.setdefault(t, []).append((h, v))
            for t in sorted(events_by_time):
                w.emit_time_change(t)
                for h, v in events_by_time[t]:
                    w.emit_value_change(h, v)
            w.close()
            # Verify fst2vcd accepts it
            result = subprocess.run(
                ["fst2vcd", "-f", writer_path],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                failed.append(f"{name} (fst2vcd rc={result.returncode})")
            else:
                print(f"OK   {name}: writer roundtrip accepted by fst2vcd")
            Path(writer_path).unlink()
        except Exception as e:
            failed.append(f"{name} ({e})")
    return failed



def main():
    if not FST_FILES:
        print("No FST fixtures found. Run build.ps1 first.")
        sys.exit(1)

    total_matched = 0
    total_mismatched = 0
    failed_files = []

    for fst_path in FST_FILES:
        name = fst_path.stem
        try:
            matched, mismatched, details = compare_events(fst_path)
            total_matched += matched
            total_mismatched += mismatched
            if mismatched:
                failed_files.append(name)
                print(f"FAIL {name}: {mismatched} mismatches (matched={matched})")
                for d in details[:10]:
                    print(d)
                if len(details) > 10:
                    print(f"  ... and {len(details)-10} more")
            else:
                print(f"OK   {name}: {matched} events matched")
        except Exception as e:
            failed_files.append(name)
            print(f"ERROR {name}: {e}")

    print()
    print(f"Total: {total_matched} matched, {total_mismatched} mismatched")
    if failed_files:
        print(f"Failed: {failed_files}")
        sys.exit(1)

    # Writer roundtrip: for each fixture, reconstruct scenario with writer,
    # verify fst2vcd accepts output.
    writer_failed = _verify_writer_roundtrips()
    if writer_failed:
        print(f"Writer roundtrip failed: {writer_failed}")
        sys.exit(1)
    print("All golden tests passed.")


if __name__ == "__main__":
    main()

