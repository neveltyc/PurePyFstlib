<p align="center">
  <h1 align="center">PurePyFstlib</h1>
  <p align="center">
    A pure-Python FST (Fast Signal Trace) reader and conservative writer
    for portable waveform-debug tooling &mdash; no C extensions, no platform binding headaches.
  </p>
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-0.2.3-3366cc?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10+-3366cc?style=flat-square&logo=python&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-3366cc?style=flat-square">
</p>

<p align="right"><a href="README_zh.md">中文</a></p>

---

## Why PurePyFstlib?

FST is increasingly useful for waveform-debug workflows because it is compact, GTKWave-friendly, and much easier to transmit than large VCD files. However, many Python workflows still depend on C-backed FST bindings. That creates friction:

- prebuilt `pylibfst` packages are not always available on every platform;
- Windows users may need local source builds and toolchain setup;
- CI and agent environments often prefer pure-Python, source-distributable packages;
- waveform tools that only need reading, slicing, metadata reporting, or compact artifact generation do not always need the full C writer stack.

PurePyFstlib takes a deliberately asymmetric approach:

- **Reader first:** accept as many common real-world FST files as possible.
- **Writer conservative:** emit one stable, GTKWave-compatible FST subset instead of chasing every high-performance libfst writer path.
- **No C extension dependency:** import and run as normal Python code.
- **Tooling oriented:** useful as a backend for waveform filters, slicers, reporters, and agent-assisted RTL debug tools.

---

## Design position

PurePyFstlib is not intended to be a drop-in performance replacement for GTKWave `libfst`.

The reader aims to cover most common FST structures used by existing tools. The writer intentionally emits a smaller and conservative format: gzip-compressed hierarchy and zlib-compressed VCDATA. This is sufficient for use cases such as waveform slicing, VCD/FST filtering, compact debug artifacts, and GTKWave-readable outputs.

The main expected use case is not full simulator waveform dumping. A more realistic use case is:

```text
large VCD/FST
→ select time window
→ select signal subset
→ preserve initial state at the slice boundary
→ write a compact FST
→ open directly in GTKWave or pass to another debug tool
```

---

## Install

```bash
git clone https://github.com/neveltyc/PurePyFstlib.git
cd PurePyFstlib
pip install -e .
```

Alternatively build a wheel:

Build a wheel:

```bash
python -m pip wheel . -w dist
```

The package is pure Python and requires no C compiler for normal installation.

---

## Quick start

Read waveform files and inspect signals, metadata, and value changes.

```python
from truepyfstlib import FstReader

# What's in this file?
r = FstReader("waveform.fst")
print(r.summary())

# List all signals
for var in r.vars():
    print(var.handle, var.full_name, var.length)

# Read raw value changes for one handle
for time, value in r.iter_value_changes_all(handle=1, include_initial=True):
    print(time, value)

# Read decoded values (float for real, str for vectors, bytes for strings)
for time, value in r.iter_decoded_value_changes_all(handle=1, include_initial=True):
    print(time, value)

# All hierarchy attributes, including vendor/unknown payloads
for item in r.attribute_report(decoded=True):
    print(item)

# Per-signal SV/VHDL metadata
meta = r.metadata_for_handle(1)
print(meta)

# Apply blackout semantics
for time, value in r.iter_value_changes_all(
    handle=1, include_initial=True, respect_blackout=True,
):
    print(time, value)
```

```python
from truepyfstlib import FstWriter, FstScopeType, FstVarType, FstVarDir

# Write a conservative FST file (gzip hierarchy + zlib VCDATA)
w = FstWriter("slice.fst", timescale=-9)
w.set_scope(FstScopeType.VCD_MODULE, "top")
clk = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "clk")
data = w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 8, "data")
w.set_upscope()

w.emit_time_change(0)
w.emit_value_change_bit(clk, 0)
w.emit_value_change(data, b"00000000")

w.emit_time_change(10)
w.emit_value_change_bit(clk, 1)
w.emit_value_change(data, b"10101010")

w.close()
```

The writer output is intentionally conservative: gzip hierarchy plus zlib VCDATA.

---

## Reader support matrix

| Area | Status | Notes |
|---|---:|---|
| FST header (`HDR`) | Supported | Includes version, date, timescale, timezero, file type, counts. |
| Geometry (`GEOM`) | Supported | Used for frame layout when present. |
| GEOM-less fallback | Supported | Signal width/type can be inferred from hierarchy for compatible files. |
| Hierarchy (`HIER`) | Supported | gzip hierarchy blocks. |
| LZ4 hierarchy (`HIER_LZ4`) | Supported | Pure-Python LZ4 block decompression. |
| LZ4DUO hierarchy (`HIER_LZ4DUO`) | Supported | Common layout support; needs more external corpus testing. |
| Whole-file wrapper (`ZWRAPPER`) | Supported | gzip/raw-deflate wrapper handling. |
| Static VCDATA | Supported | Frame, time table, chain table, per-handle value iteration. |
| Dynamic alias VCDATA | Supported | `VCDATA_DYN_ALIAS` and `VCDATA_DYN_ALIAS2` parsing paths are implemented. |
| zlib packed chains | Supported | Standard zlib chain decoding. |
| LZ4 packed chains | Supported | Pure-Python raw LZ4 block decompression. |
| FastLZ packed chains | Supported | Pure-Python FastLZ decompression. |
| Multi-section VCDATA | Supported | Section iteration and all-section iterators are available. |
| Scalar and vector values | Supported | Raw and decoded accessors. |
| String values (`GEN_STRING`) | Supported | Variable-length string payloads are exposed as bytes. |
| Real values | Supported | Raw bytes and decoded Python float helpers. |
| Aliases | Supported | One handle can map to multiple hierarchy names. |
| Blackout sections | Supported | Raw intervals plus optional semantic filtering in iterators. |
| SV/VHDL supplemental metadata | Supported | Common libfst-style attributes are parsed and attached to variables. |
| Unknown/vendor attributes | Supported as payload reports | Raw bytes are preserved and exposed as escaped ASCII, UTF-8/Latin-1 views, hex, and base64. |
| VCD extension hierarchy lines | Diagnostic support | `iter_vcd_extension_lines()` provides hierarchy-extension text for inspection, not a full VCD exporter. |
| Parallel `.hier` side-file mode | Not supported | Explicitly out of scope for now. |

---

## Writer support matrix

| Area | Status | Notes |
|---|---:|---|
| FST header (`HDR`) | Supported | Conservative header generation. |
| Geometry (`GEOM`) | Supported | Fixed-width, string, and real geometry. |
| Hierarchy (`HIER`) | Supported | gzip hierarchy output. |
| VCDATA | Supported | zlib-packed conservative writer path. |
| Scalar/vector values | Supported | 1-bit and N-bit ASCII bit values, including common unknown/high-impedance symbols. |
| String values | Supported | `GEN_STRING` variable-length payloads. |
| Real values | Supported | double payloads. |
| Aliases | Supported | libfst-style alias handling. |
| Multi-section output | Supported | `flush_context()` can create multiple sections. |
| Empty/no-change files | Supported | Frame-only section output. |
| Blackout sections | Supported | `emit_dump_active()` writes blackout records. |
| Common metadata helpers | Supported | comments, env vars, value lists, source stems, enum table references, supplemental variables. |
| Writer-side FastLZ | Not supported | Reader can decode FastLZ, but writer intentionally emits zlib. |
| Writer-side LZ4 | Not supported | Reader can decode LZ4, but writer intentionally emits zlib. |
| HIER_LZ4 writer | Not supported | gzip hierarchy is the conservative writer target. |
| Parallel writer | Not supported | Python writer is not designed as a high-performance simulator dump backend. |
| Repack-on-close / dump-size-limit paths | Not supported | libfst performance/packing features are intentionally out of scope. |

---

## Not supported / intentionally out of scope

PurePyFstlib does not try to reproduce every internal path of GTKWave `libfst`.

Current explicit non-goals:

- replacing `libfst` as a high-performance simulator dump writer;
- writer-side FastLZ/LZ4 compression;
- writer-side LZ4 hierarchy;
- parallel writer and parallel `.hier` side-file support;
- repack-on-close and dump-size-limit workflows;
- full `fst2vcd` replacement;
- vendor-specific semantic interpretation of every unknown private attribute.

For unknown or third-party attributes, the reader preserves and reports the payload instead of guessing tool-specific meaning.

---

## Typical use cases

- Inspect FST files from Python without C extension bindings.
- Build waveform-debug agents that can query signal names, values, metadata, and time windows.
- Convert filtered/cropped waveform data into compact GTKWave-readable FST artifacts.
- Build VCD/FST slicing tools that keep only the relevant time range and signal subset.
- Generate small waveform files for bug reports, CI artifacts, or cross-platform debug sessions.

---

## Project layout

```
src/truepyfstlib/       FST library (pure Python, stdlib only)
verify/                 Test suite with fixtures and golden cross-checks
CHANGELOG.md            Per-version release details
```

---

## Tests

Run the pure-Python checks:

```bash
# Linux / macOS
PYTHONPATH=src python verify/roundtrip.py
PYTHONPATH=src python verify/verify_reader.py
PYTHONPATH=src python verify/verify_writer.py

# Windows (PowerShell)
$env:PYTHONPATH = "src"; python verify/roundtrip.py; python verify/verify_reader.py; python verify/verify_writer.py
```

Golden fixture cross-validation (requires GTKWave `fst2vcd`):

```bash
PYTHONPATH=src python verify/verify_golden.py
```

When GTKWave tools are installed, `verify_writer.py` can additionally validate that generated FST files are accepted by `fst2vcd`. This is recommended before publishing writer-facing releases.

Build check:

```bash
python -m pip wheel . -w dist
python -c "import truepyfstlib; print(truepyfstlib.__version__)"
```

---

## Version history

---

## License

MIT.

This project is a pure-Python implementation for portable FST tooling. It does not require linking against `pylibfst` or GTKWave `libfst` at install time.
