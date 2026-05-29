#!/usr/bin/env python3
"""Compare TruePyFstlib reader output against fst2vcd for arbitrary FST files.

Unlike verify_golden.py (which is scoped to verify/fixtures), this takes a list
of FST paths on the command line and is meant for ad-hoc validation against
real-world files such as GTKWave's examples (des.fst, transaction.fst) and the
libfst test corpus.  It reuses verify_golden's VCD parser and comparison.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_golden import compare_events  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: crosscheck.py FILE.fst [FILE.fst ...]")
        return 2
    fails = 0
    for arg in argv:
        p = Path(arg)
        try:
            matched, mismatched, details = compare_events(p)
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {p.name}: {e}")
            fails += 1
            continue
        if mismatched:
            print(f"FAIL {p.name}: {mismatched} mismatches (matched={matched})")
            for d in details[:12]:
                print(d)
            if len(details) > 12:
                print(f"  ... and {len(details) - 12} more")
            fails += 1
        else:
            print(f"OK   {p.name}: {matched} events matched")
    print()
    print("ALL OK" if not fails else f"{fails} file(s) failed")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
