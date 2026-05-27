# Changelog

## 0.2.0

Reader enhancements:

- GEOM-less file compatibility: derive signal lengths/types from hierarchy
  when geometry block is missing (matches libfst fallback behavior)
- FST_BL_SKIP end marker: _scan_blocks stops at 0xff instead of requiring
  full 9-byte block header
- Empty/frame-only VCDATA sections: iter_time_value_pairs handles sections
  with no value changes, returning frame snapshot at beg_time
- Real value decoding: decode_value returns Python float for real handles
  using header endian marker; fixed-width signals return ASCII string;
  string signals return raw bytes
- New decode APIs: get_initial_value_decoded, iter_decoded_value_changes,
  iter_decoded_value_changes_all
- Cross-section aggregation: iter_value_changes_all with include_initial
  flag; iter_time_value_pairs_all
- Chain scheduling boundary protection: tdelta/remaining guards
  prevent index errors on malformed or empty chain data
- New test file: verify/verify_reader.py (4 tests)


## 0.1.0

Writer fully aligned with libfst C API:

- Real-valued variables (VCD_REAL, REAL_PARAMETER, REALTIME, SV_SHORTREAL):
  geometry=0, 8-byte IEEE-754 double payloads, NaN initial frame
- Numeric emit helpers: emit_value_change_real, emit_value_change32/64,
  emit_value_change_vec32/64, emit_variable_length_value_change
- Metadata helpers: set_timezero, set_timescale_from_string,
  set_comment, set_env_var, set_value_list, set_source_stem,
  set_source_instantiation_stem
- Enum tables: create_enum_table, emit_enum_table_ref
- create_var2: fstWriterCreateVar2 API with supplemental var/data types
- emit_value_change before first time change updates section initial frame
  instead of creating VC records (matches fstapi.c behavior)
- Alias follows C writer: unknown handle → new canonical, no metadata rejection
- Attr validation permissive (normalizes like fstapi.c)

Writer semantic fixes:

- Multi-section: _section_initial_values tracks per-section start state,
  flush snapshots beginning (not end) values, next section inherits
- Empty/no-change files: frame-only VCDATA section emitted when variables
  exist but no value changes
- Frame snapshot: string 0 bytes, real 8-byte NaN, N-bit wire initialized to x
- Zero-length non-string vars rejected in create_var
- emit_value_change accepts Python str for string vars (auto UTF-8 encode)
- GEN_STRING auto-detected by var_type (no manual is_string=True needed)

Writer API guards:

- Hierarchy freeze: create_var/set_scope disabled after first emit_time_change
- Close guard: all mutation raises RuntimeError after close()
- emit_value_change_bit: strict 0/1 validation
- Alias must match canonical metadata (or new canonical if unknown handle)

Reader improvements:

- timezero parsed as signed int64 (struct.unpack(">q"))
- Blackout intervals accessible via .blackouts property
- _time_to_index dict for O(1) cumulative time lookups
- _frame_prefix for O(1) frame data offset resolution
- Unused string handles yield nothing (C reader behavior)
- Docstring updated to reflect actually supported features

Golden fixture verification:

- verify/verify_golden.py: reader vs fst2vcd cross-validation + writer
  roundtrip accepted by fst2vcd (6 fixtures, 54 events, 0 mismatches)
- verify/verify_writer.py: 29 regression tests covering minimal toggle,
  sparse, string, alias, multi-section, x/z values, N-bit vectors,
  real signals, numeric helpers, enum tables, hierarch freeze, close guard

## 0.0.3

- Fix 5 writer bugs: HIER gzip, memory_required, chain sentinel, write_varint
  byte order, x/z encoding
- Writer-generated FST files now pass fst2vcd verification

## 0.0.2

- Fix DYN_ALIAS2 chain parsing: LEB128 signed varint, cumulative time deltas
- Fix duplicate initial value for signals with chain data
- Reorganize: tests/ -> verify/, test_fixtures/ -> verify/fixtures/

## 0.0.1

Initial release
