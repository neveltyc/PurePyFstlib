#!/usr/bin/env bash
# Portable fixture builder: compile each *.v testbench with iverilog, run it to
# produce a VCD, then convert to FST with gtkwave's vcd2fst.  Mirrors build.ps1
# but runs on Linux/macOS shells.  The generated *.fst files are the golden
# fixtures consumed by verify_golden.py.
set -euo pipefail
cd "$(dirname "$0")"

shopt -s nullglob
for src in *.v; do
    base="${src%.v}"
    echo "Building $src ..."
    iverilog -o "${base}.vvp" "$src"
    # vvp writes the VCD named by $dumpfile inside the source.
    vvp "${base}.vvp" >/dev/null

    # Locate the VCD that was just produced (named per $dumpfile).
    vcd=""
    if [ -f "${base}.vcd" ]; then
        vcd="${base}.vcd"
    else
        dumpname=$(grep -oE '\$dumpfile\("[^"]+"\)' "$src" | head -1 | sed -E 's/.*\("([^"]+)"\).*/\1/')
        if [ -n "${dumpname}" ] && [ -f "${dumpname}" ]; then
            vcd="${dumpname}"
        fi
    fi
    if [ -z "${vcd}" ]; then
        echo "  WARNING: no VCD produced for ${src}, skipping" >&2
        rm -f "${base}.vvp"
        continue
    fi

    vcd2fst "${vcd}" "${base}.fst" >/dev/null 2>&1
    rm -f "${base}.vvp" "${vcd}"
    printf '  -> %s  %s bytes\n' "${base}.fst" "$(stat -c%s "${base}.fst" 2>/dev/null || stat -f%z "${base}.fst")"
done

echo "Done."
ls -1 *.fst 2>/dev/null || true
