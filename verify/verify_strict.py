#!/usr/bin/env python3
"""Strict reader-vs-fst2vcd comparison keyed on full hierarchical paths.

verify_golden.py matches on leaf signal names, which is fragile for designs
with repeated leaf names across scopes (generate blocks) or aliases.  This
comparison instead reconstructs the *full* scope path for every VCD identifier
from the $scope/$upscope/$var structure, and compares the reader's full_name
event stream against it.  Real-valued signals are compared numerically, and
the initial NaN that fst2vcd omits at t=start is tolerated.

Exit status is nonzero on any genuine mismatch.
"""
from __future__ import annotations

import math
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from truepyfstlib import FstReader  # noqa: E402


def parse_vcd_full(vcd_text: str):
    """Return ({(time, fullpath): value}, {fullpath: width}, real_ids)."""
    id_to_paths: dict[str, list[str]] = {}
    id_is_real: dict[str, bool] = {}
    width: dict[str, int] = {}
    scope_stack: list[str] = []
    events: dict[tuple[int, str], object] = {}
    current_time = 0
    in_body = False

    def emit(sig_id: str, value: object) -> None:
        for full in id_to_paths.get(sig_id, ()):  # one id may map to many paths
            events[(current_time, full)] = value

    for raw in vcd_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("$scope"):
            parts = line.split()
            if len(parts) >= 3:
                scope_stack.append(parts[2])
        elif line.startswith("$upscope"):
            if scope_stack:
                scope_stack.pop()
        elif line.startswith("$var"):
            parts = line.split()
            if len(parts) >= 5:
                vtype = parts[1]
                w = int(parts[2])
                sig_id = parts[3]
                sig_name = parts[4]
                full = ".".join(scope_stack + [sig_name])
                id_to_paths.setdefault(sig_id, []).append(full)
                id_is_real[sig_id] = (vtype == "real")
                width[full] = w
        elif line.startswith("$enddefinitions"):
            in_body = True
        elif line.startswith("$"):
            continue
        elif line.startswith("#"):
            current_time = int(line[1:])
        elif in_body:
            c0 = line[0]
            if c0 == "b":
                body, _, sig_id = line[1:].partition(" ")
                emit(sig_id, body)
            elif c0 == "r":
                body, _, sig_id = line[1:].partition(" ")
                emit(sig_id, ("REAL", float(body)))
            elif c0 == "s":
                body, _, sig_id = line[1:].partition(" ")
                emit(sig_id, body)
            elif c0 in "01xzXZhHuUwWlL-":
                emit(line[1:], c0)
    return events, width, id_is_real


def reader_events_full(r: FstReader):
    # Map every variable (canonical AND alias) to its handle.  Aliases share a
    # handle with their canonical signal, so the value stream for a handle must
    # be emitted under each path that references it — matching how fst2vcd lists
    # the same identifier code under every aliased scope.
    handle_to_paths: dict[int, list[str]] = {}
    for var in r.vars():
        full = var.full_name
        br = full.rfind(" [")
        if br > 0 and full.endswith("]"):
            full = full[:br]
        handle_to_paths.setdefault(var.handle, []).append(full)

    events: dict[tuple[int, str], object] = {}
    # NOTE: fst2vcd emits a separate $timezero directive and does NOT shift the
    # value-change timeline, so reader raw times already match the VCD body.
    for h, paths in handle_to_paths.items():
        is_real = r.is_real_handle(h)
        is_str = r.is_string_handle(h)
        for t, val in r.iter_value_changes(h):
            if is_real:
                v: object = ("REAL", struct.unpack("<d", val)[0])
            elif is_str:
                v = val.decode("latin-1")
            else:
                v = val.decode("ascii")
            for full in paths:
                events[(t, full)] = v
    return events


def _norm_scalar(v):
    return v


def compare(fst_path: Path) -> tuple[int, list[str]]:
    out = subprocess.run(["fst2vcd", "-f", str(fst_path)],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        return 1, [f"fst2vcd failed: {out.stderr.strip()}"]
    ref, width, _ = parse_vcd_full(out.stdout)
    r = FstReader(str(fst_path))
    try:
        got = reader_events_full(r)
        start = r.header.start_time
    finally:
        r.close()

    problems: list[str] = []
    for key, refv in ref.items():
        t, path = key
        if key not in got:
            problems.append(f"t={t} {path}: ref={refv!r} reader=MISSING")
            continue
        gv = got[key]
        if isinstance(refv, tuple) and refv[0] == "REAL":
            if not (isinstance(gv, tuple) and gv[0] == "REAL"):
                problems.append(f"t={t} {path}: real type mismatch ref={refv} got={gv}")
            elif not (math.isclose(refv[1], gv[1], rel_tol=1e-12, abs_tol=1e-12)
                      or (math.isnan(refv[1]) and math.isnan(gv[1]))):
                problems.append(f"t={t} {path}: real ref={refv[1]} got={gv[1]}")
        else:
            # normalize vector widths: fst2vcd may zero-trim; reader pads to width
            rg = gv if not isinstance(gv, tuple) else gv
            if rg != refv:
                # tolerate leading-zero / x-extension differences in vectors
                if isinstance(refv, str) and isinstance(rg, str) and rg.lstrip("0xz") == refv.lstrip("0xz") and set(refv) <= set("01xz"):
                    pass
                else:
                    problems.append(f"t={t} {path}: ref={refv!r} got={rg!r}")

    # reader-extra events at t=start that are just the initial value fst2vcd omits
    extra = set(got.keys()) - set(ref.keys())
    real_extra = [k for k in extra if k[0] != start]
    for k in sorted(real_extra)[:20]:
        problems.append(f"t={k[0]} {k[1]}: reader={got[k]!r} ref=MISSING")

    return (1 if problems else 0), problems


def main(argv):
    if not argv:
        print("usage: verify_strict.py FILE.fst [FILE.fst ...]")
        return 2
    fails = 0
    for a in argv:
        p = Path(a)
        rc, problems = compare(p)
        if rc:
            print(f"FAIL {p.name}: {len(problems)} problems")
            for pr in problems[:15]:
                print("  " + pr)
            if len(problems) > 15:
                print(f"  ... and {len(problems)-15} more")
            fails += 1
        else:
            print(f"OK   {p.name}")
    print("\n" + ("ALL OK" if not fails else f"{fails} failed"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
