#!/usr/bin/env python3
"""Performance benchmark + correctness fingerprint harness for TruePyFstlib.

Goals
-----
1. Establish a reproducible performance baseline for the reader hot paths.
2. Guard against regressions in *output* while optimizing: every benchmark that
   walks value changes also folds the decoded data into a stable fingerprint
   (a hash).  An optimization is only acceptable if the fingerprint is
   unchanged, so the same harness measures speed and proves correctness at
   once.

Usage
-----
    python verify/bench.py                       # run, print table
    python verify/bench.py --save-baseline       # run, write verify/baseline.json
    python verify/bench.py --compare             # run, diff against baseline.json
    python verify/bench.py --fixture path.fst    # benchmark a specific file
    python verify/bench.py --repeats 7           # repeats per measurement

The default fixture is verify/fixtures/bench.fst (built by build.sh from
bench.v).  If it is missing the harness falls back to the largest *.fst in the
fixtures directory.  Fingerprints are independent of timing, so they are
comparable across machines; absolute times are not, hence --compare reports
ratios and flags only large slowdowns.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

# Allow running straight from a checkout without installing.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from truepyfstlib import FstReader  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"

# A slowdown beyond this ratio (relative to the recorded baseline) is reported
# as a regression by --compare.  Generous because absolute times vary by host.
REGRESSION_RATIO = 1.30


def _default_fixture() -> Path:
    preferred = FIXTURES_DIR / "bench.fst"
    if preferred.is_file():
        return preferred
    candidates = sorted(FIXTURES_DIR.glob("*.fst"), key=lambda p: p.stat().st_size)
    if not candidates:
        raise SystemExit(
            "No FST fixtures found. Run verify/fixtures/build.sh first "
            "(needs iverilog + vcd2fst)."
        )
    return candidates[-1]


class _Hasher:
    """Order-sensitive incremental fingerprint over (key, time, value) stream."""

    __slots__ = ("_h", "_count")

    def __init__(self) -> None:
        self._h = hashlib.blake2b(digest_size=16)
        self._count = 0

    def update(self, *parts) -> None:
        for p in parts:
            if isinstance(p, bytes):
                self._h.update(p)
            elif isinstance(p, str):
                self._h.update(p.encode("utf-8", "surrogatepass"))
            else:
                self._h.update(repr(p).encode("ascii"))
            self._h.update(b"\x1f")
        self._h.update(b"\x1e")
        self._count += 1

    def hexdigest(self) -> str:
        return self._h.hexdigest()

    @property
    def count(self) -> int:
        return self._count


# ---------------------------------------------------------------------------
# Workloads.  Each returns (fingerprint_hex, item_count) and is pure w.r.t. the
# file, so repeated calls give identical fingerprints.
# ---------------------------------------------------------------------------

def wl_open_only(path: Path) -> tuple[str, int]:
    r = FstReader(str(path))
    try:
        h = _Hasher()
        h.update(r.header.max_handle, r.header.start_time, r.header.end_time,
                 len(r.vc_sections))
        return h.hexdigest(), r.header.max_handle
    finally:
        r.close()


def wl_per_signal(path: Path) -> tuple[str, int]:
    r = FstReader(str(path))
    try:
        h = _Hasher()
        for handle in range(1, r.header.max_handle + 1):
            for t, val in r.iter_value_changes(handle):
                h.update(handle, t, val)
        return h.hexdigest(), h.count
    finally:
        r.close()


def wl_all_signals(path: Path) -> tuple[str, int]:
    r = FstReader(str(path))
    try:
        h = _Hasher()
        for t, changes in r.iter_time_value_pairs_all():
            for handle, val in changes:
                h.update(t, handle, val)
        return h.hexdigest(), h.count
    finally:
        r.close()


def wl_decoded_per_signal(path: Path) -> tuple[str, int]:
    r = FstReader(str(path))
    try:
        h = _Hasher()
        for handle in range(1, r.header.max_handle + 1):
            for t, val in r.iter_decoded_value_changes_all(handle):
                h.update(handle, t, str(val))
        return h.hexdigest(), h.count
    finally:
        r.close()


def wl_window_queries(path: Path) -> tuple[str, int]:
    """Random-ish point queries: value of every signal at evenly spaced times."""
    r = FstReader(str(path))
    try:
        h = _Hasher()
        start = r.header.start_time
        end = r.header.end_time
        span = max(1, end - start)
        steps = 32
        for k in range(steps):
            t = start + (span * k) // steps
            for handle in range(1, r.header.max_handle + 1):
                v = r.get_value_at(handle, t)
                h.update(handle, t, v if v is not None else b"")
        return h.hexdigest(), h.count
    finally:
        r.close()


WORKLOADS = {
    "open_only": wl_open_only,
    "per_signal": wl_per_signal,
    "all_signals": wl_all_signals,
    "decoded_per_signal": wl_decoded_per_signal,
    "window_queries": wl_window_queries,
}


def _measure(fn, path: Path, repeats: int) -> dict:
    times: list[float] = []
    fingerprint = None
    count = 0
    for _ in range(repeats):
        t0 = time.perf_counter()
        fp, cnt = fn(path)
        t1 = time.perf_counter()
        times.append(t1 - t0)
        if fingerprint is None:
            fingerprint, count = fp, cnt
        elif fp != fingerprint:
            raise AssertionError(
                f"non-deterministic fingerprint for workload {fn.__name__}"
            )
    return {
        "median_s": statistics.median(times),
        "min_s": min(times),
        "count": count,
        "fingerprint": fingerprint,
    }


def run(path: Path, repeats: int) -> dict:
    results = {}
    for name, fn in WORKLOADS.items():
        results[name] = _measure(fn, path, repeats)
    return {
        "fixture": path.name,
        "fixture_bytes": path.stat().st_size,
        "repeats": repeats,
        "results": results,
    }


def _fmt_rate(count: int, seconds: float) -> str:
    if seconds <= 0:
        return "-"
    return f"{count / seconds / 1e6:.2f}M/s"


def print_table(report: dict) -> None:
    print(f"Fixture: {report['fixture']} ({report['fixture_bytes']:,} bytes), "
          f"repeats={report['repeats']}")
    print(f"{'workload':22} {'median':>10} {'min':>10} {'items':>10} {'rate':>10}")
    print("-" * 66)
    for name, m in report["results"].items():
        print(f"{name:22} {m['median_s']*1e3:>8.2f}ms {m['min_s']*1e3:>8.2f}ms "
              f"{m['count']:>10,} {_fmt_rate(m['count'], m['min_s']):>10}")
    print("\nfingerprints:")
    for name, m in report["results"].items():
        print(f"  {name:22} {m['fingerprint']}")


def compare(report: dict, baseline: dict) -> int:
    base_results = baseline.get("results", {})
    print(f"Comparing against baseline (fixture {baseline.get('fixture')}, "
          f"recorded {baseline.get('fixture_bytes'):,} bytes)\n")
    print(f"{'workload':22} {'base':>10} {'now':>10} {'ratio':>8}  status")
    print("-" * 70)
    regressed = False
    fp_mismatch = False
    for name, m in report["results"].items():
        b = base_results.get(name)
        if b is None:
            print(f"{name:22} {'--':>10} {m['min_s']*1e3:>8.2f}ms {'NEW':>8}  new workload")
            continue
        ratio = m["min_s"] / b["min_s"] if b["min_s"] > 0 else float("inf")
        status = "ok"
        if m["fingerprint"] != b["fingerprint"]:
            status = "FINGERPRINT CHANGED!"
            fp_mismatch = True
        elif ratio > REGRESSION_RATIO:
            status = f"SLOWER x{ratio:.2f}"
            regressed = True
        elif ratio < (1 / REGRESSION_RATIO):
            status = f"faster x{1/ratio:.2f}"
        print(f"{name:22} {b['min_s']*1e3:>8.2f}ms {m['min_s']*1e3:>8.2f}ms "
              f"{ratio:>7.2f}x  {status}")
    print()
    if fp_mismatch:
        print("FAIL: at least one fingerprint changed — output is NOT equivalent "
              "to the baseline.")
        return 2
    if regressed:
        print("WARN: at least one workload regressed beyond the allowed ratio.")
        return 1
    print("OK: fingerprints match baseline; no significant regressions.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", type=Path, default=None,
                    help="FST file to benchmark (default: verify/fixtures/bench.fst)")
    ap.add_argument("--repeats", type=int, default=5,
                    help="measurements per workload (default 5; reports median+min)")
    ap.add_argument("--save-baseline", action="store_true",
                    help="write results to verify/baseline.json")
    ap.add_argument("--compare", action="store_true",
                    help="compare results to verify/baseline.json and exit nonzero on regression")
    ap.add_argument("--json", action="store_true", help="emit raw JSON report")
    args = ap.parse_args()

    path = args.fixture if args.fixture else _default_fixture()
    if not path.is_file():
        raise SystemExit(f"fixture not found: {path}")

    report = run(path, args.repeats)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_table(report)

    if args.save_baseline:
        BASELINE_PATH.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nBaseline written to {BASELINE_PATH}")

    if args.compare:
        if not BASELINE_PATH.is_file():
            raise SystemExit(f"no baseline at {BASELINE_PATH}; run --save-baseline first")
        baseline = json.loads(BASELINE_PATH.read_text())
        sys.exit(compare(report, baseline))


if __name__ == "__main__":
    main()
