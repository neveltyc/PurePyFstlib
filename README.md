<p align="center">
  <h1 align="center">PurePyFstlib</h1>
  <p align="center">
    A pure-Python FST (Fast Signal Trace) reader and conservative writer
    for portable waveform-debug tooling &mdash; no C extensions, no platform binding headaches.
  </p>
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-0.4.1-3366cc?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10+-3366cc?style=flat-square&logo=python&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-3366cc?style=flat-square">
</p>

<p align="right"><a href="README_zh.md">中文</a></p>

---

## What is PurePyFstlib?

PurePyFstlib is a pure-Python implementation of the FST waveform format.

It is designed for Python waveform tooling that needs to **read real-world FST files**, inspect signal metadata, perform block/section/chain-level random access, and write a conservative GTKWave-readable FST subset for filtered waveform artifacts.

The project is intentionally asymmetric:

- **Reader first.** The reader aims to support most common FST files produced by existing tools.
- **Writer conservative.** The writer emits a small, stable subset: gzip-compressed hierarchy and zlib-compressed VCDATA.
- **No C extension dependency.** It does not require `pylibfst`, GTKWave `libfst`, local C compilation, or platform-specific wheels.
- **Tooling oriented.** It is meant to be used by waveform filters, slicers, reporters, and agent-assisted RTL debug tools.

PurePyFstlib is **not** intended to replace GTKWave `libfst` as a high-performance simulator dump backend.

---

## Why not just use `pylibfst`?

`pylibfst` and GTKWave `libfst` are the right choice when you need the native C implementation. However, they are less convenient when the goal is portable Python tooling:

- prebuilt packages are not always available on every platform;
- Windows users may need local source builds and a C toolchain;
- CI, sandbox, and agent environments often prefer source-distributable pure-Python packages;
- many debug tools need FST reading, slicing, reporting, or compact artifact generation, not a full simulator-grade writer stack.

PurePyFstlib exists to make FST usable in these environments.

---

## Design position

PurePyFstlib keeps the reader and writer roles separate.

### Reader

The reader is the main value of this project. It tries to parse common FST structures broadly and expose stable low-level access primitives:

```text
FST file
→ block index
→ hierarchy / geometry
→ signal table
→ VCDATA section table
→ handle-level chain access
→ raw / decoded values
→ metadata and blackout information
```

Reader APIs are designed to support tools such as waveform analyzers, wavecut/filter utilities, and agent workflows.

### Writer

The writer intentionally emits a conservative FST subset:

```text
HDR + GEOM + gzip HIER + zlib VCDATA
```

This is sufficient for post-processing workflows such as:

```text
large VCD/FST
→ select time window
→ select signal subset
→ preserve initial state at the slice boundary
→ write a compact FST
→ open directly in GTKWave or pass to another debug tool
```

The writer does not try to reproduce every internal path of `libfst`, such as parallel writer modes, repack-on-close, writer-side LZ4/FastLZ packing, or dump-size-limit workflows.

---

## Install

Development install:

```bash
git clone https://github.com/neveltyc/PurePyFstlib.git
cd PurePyFstlib
pip install -e .
```

Build a wheel:

```bash
python -m pip wheel . -w dist
```

The package is pure Python and requires no C compiler for normal installation.

---

## Quick start

### File and signal inspection

```python
from truepyfstlib import FstReader

with FstReader("waveform.fst") as r:
    print(r.file_info())

    for sig in r.signal_table():
        print(sig["handle"], sig["path"], sig["width"], sig["type"])
```

### Resolve a signal and read value changes

```python
from truepyfstlib import FstReader

with FstReader("waveform.fst") as r:
    handle = r.resolve_handle("top.u0.state")

    for time, value in r.iter_value_changes_range(
        handle,
        start=1000,
        end=2000,
        include_initial=True,
    ):
        print(time, value)
```

### Random access by signal and time

```python
from truepyfstlib import FstReader

with FstReader("waveform.fst") as r:
    handle = r.resolve_handle("top.u0.state")

    # Value of one signal at one time point.
    print(r.get_value_at(handle, 1500, decoded=True))

    # Snapshot of selected signals at one time point.
    handles = r.find_handles("top.u0.*")
    snap = r.snapshot_at(1500, handles=handles, decoded=True)
    print(snap)
```

### Selected event stream for analyzers or wavecut tools

```python
from truepyfstlib import FstReader

with FstReader("waveform.fst") as r:
    handles = r.find_handles("top.u0.*")

    for time, changes in r.iter_event_groups(
        start=1000,
        end=2000,
        handles=handles,
        decoded=True,
        include_initial=True,
    ):
        print(time, changes)
```

### Metadata, attributes, and blackout information

```python
from truepyfstlib import FstReader

with FstReader("waveform.fst") as r:
    print(r.blackouts())
    print(r.is_dump_active_at(1000))

    for attr in r.attribute_report(decoded=True):
        print(attr)

    meta = r.metadata_for_handle(1)
    print(meta)
```

### Write a conservative FST file

```python
from truepyfstlib import FstWriter, FstScopeType, FstVarType, FstVarDir

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

---

## Stable reader API

This section is the intended public API surface for reader users. It is grouped by role so that waveform analyzers can depend on stable primitives instead of internal parser details.

### File-level getters

These methods follow the spirit of `libfst` reader getters, but use Python naming.

```python
r.get_version_string()
r.get_date_string()
r.get_file_type()
r.get_var_count()
r.get_scope_count()
r.get_alias_count()
r.get_start_time()
r.get_end_time()
r.get_timescale()
r.get_timezero()
r.get_value_change_section_count()
r.get_max_handle()
r.get_value_from_handle_at_time(handle, time, decoded=False)
```

### Stable structure tables

Use these for integration with analyzer scripts, CLI tools, and JSON-facing workflows.

```python
r.file_info()       # structured file overview
r.block_table()     # top-level FST block table
r.signal_table()    # signal records: handle, path, aliases, width, type, metadata summary
r.section_table()   # VCDATA section records: index, begin/end time, chain count, pack type
```


### Signal lookup

Reader-level lookup is intentionally simple: exact name, glob, or regex. CLI-specific substring filtering and condition parsing belong in analyzer tools.

```python
r.signal_names(include_aliases=True)
r.names_for_handle(handle)

r.find_handle(name, include_aliases=True)
r.find_handles(pattern=None, regex=False, include_aliases=True, unique=True)

r.find_signal(name, include_aliases=True)
r.find_signals(pattern=None, regex=False, include_aliases=True, unique=True)

r.resolve_handle(query, regex=False, include_aliases=True)
```

Recommended convention:

- use `find_handle()` when the input is an exact signal name;
- use `find_handles()` / `find_signals()` when multiple matches are expected;
- use `resolve_handle()` when the caller requires exactly one signal.

### Section and time access

FST VCDATA is block/section based. These APIs expose the random-time structure without forcing a full waveform scan.

```python
r.vc_sections()
r.sections_overlapping(start=None, end=None)
r.section_for_time(time)
r.section_at_time(time)   # alias of section_for_time()
```

### Value access

Use these APIs for signal-local access. They avoid decoding unrelated handles.

```python
r.get_initial_value(handle, section_index=0)
r.get_initial_value_decoded(handle, section_index=0)

r.iter_value_changes(handle, section_index=0, respect_blackout=False)
r.iter_value_changes_all(handle, include_initial=False, respect_blackout=False)
r.iter_value_changes_range(
    handle,
    start=None,
    end=None,
    include_initial=False,
    respect_blackout=False,
)

r.get_value_at(handle_or_name, time, decoded=False, respect_blackout=False)
```

Decoded variants are also available:

```python
r.decode_value(handle, value)
r.format_value(handle, value)

r.iter_decoded_value_changes(...)
r.iter_decoded_value_changes_all(...)
r.iter_decoded_value_changes_range(...)
```

### Event streams for analyzers and wavecut tools

These are thin reader-level primitives. They do not implement condition parsing, protocol interpretation, or debug reporting.

```python
r.iter_events(
    start=None,
    end=None,
    handles=None,
    decoded=False,
    include_initial=False,
    respect_blackout=False,
)

r.iter_event_groups(
    start=None,
    end=None,
    handles=None,
    decoded=False,
    include_initial=False,
    respect_blackout=False,
)

r.iter_selected_changes(
    handles,
    start=None,
    end=None,
    include_initial=False,
    decoded=False,
    respect_blackout=False,
)

r.snapshot_at(
    time,
    handles=None,
    decoded=False,
    respect_blackout=False,
)
```

Intended split:

- `FstReader`: signal/section/event access;
- analyzer layer: conditions, comparisons, statistics, reporting;
- wavecut/filter layer: signal selection, time-window slicing, output writing.

### Metadata and attributes

```python
r.metadata_for_handle(handle)

r.blackouts()
r.is_dump_active_at(time)
r.iter_blackout_intervals(start=None, end=None)

r.attributes(decoded=False)
r.attributes_for_handle(handle, decoded=False)
r.attribute_payload(attr)
r.attribute_report(decoded=True)
r.attribute_report_text()
r.iter_vcd_extension_lines()
```

Unknown/vendor attributes are preserved. The reader exposes raw bytes plus report-friendly views such as escaped ASCII, UTF-8/Latin-1 views, hex, and base64. It does not guess vendor-specific business semantics.

---

## Reader support matrix

| Area | Status | Notes |
|---|---:|---|
| FST header (`HDR`) | Supported | Version, date, timescale, timezero, file type, counts. |
| Geometry (`GEOM`) | Supported | Used for frame layout when present. |
| GEOM-less fallback | Supported | Signal width/type can be inferred from hierarchy for compatible files. |
| Hierarchy (`HIER`) | Supported | gzip hierarchy blocks. |
| LZ4 hierarchy (`HIER_LZ4`) | Supported | Pure-Python LZ4 block decompression. |
| LZ4DUO hierarchy (`HIER_LZ4DUO`) | Supported | Common layout support; external corpus testing is still recommended. |
| Whole-file wrapper (`ZWRAPPER`) | Supported | gzip/raw-deflate wrapper handling. |
| Static VCDATA | Supported | Frame, time table, chain table, per-handle value iteration. |
| Dynamic alias VCDATA | Supported | `VCDATA_DYN_ALIAS` and `VCDATA_DYN_ALIAS2` parsing paths are implemented. |
| zlib packed chains | Supported | Standard zlib chain decoding. |
| LZ4 packed chains | Supported | Pure-Python raw LZ4 block decompression. |
| FastLZ packed chains | Supported | Pure-Python FastLZ decompression. |
| Multi-section VCDATA | Supported | Section iteration and all-section iterators are available. |
| Block/section random access | Supported | Section time index plus handle-level chain access. |
| Scalar and vector values | Supported | Raw, decoded, and formatted accessors. |
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

```text
src/truepyfstlib/
  common.py        FST enums, dataclasses, and shared structures
  compression.py   pure-Python LZ4/FastLZ decompression helpers
  reader.py        FST reader, metadata, random-access iterators, report APIs
  varint.py        FST varint encoding/decoding
  writer.py        conservative FST writer

verify/
  roundtrip.py        small reader/writer roundtrip checks
  verify_reader.py    reader compatibility checks
  verify_writer.py    writer checks; can use fst2vcd when available
  verify_golden.py    golden fixture validation helper
```

---

## Tests

Run pure-Python checks:

```bash
# Linux / macOS
PYTHONPATH=src python verify/roundtrip.py
PYTHONPATH=src python verify/verify_reader.py
PYTHONPATH=src python verify/verify_writer.py

# Windows (PowerShell)
$env:PYTHONPATH = "src"; python verify/roundtrip.py; python verify/verify_reader.py; python verify/verify_writer.py
```

Golden fixture cross-validation, if available:

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

See [`CHANGELOG.md`](CHANGELOG.md).

---

## License

MIT.

This project is a pure-Python implementation for portable FST tooling. It does not require linking against `pylibfst` or GTKWave `libfst` at install time.
