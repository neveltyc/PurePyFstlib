# Changelog

## 0.2.3

Attribute payload preservation and reporting:

- FstAttrBegin.name_raw: raw C-string bytes preserved for third-party/
  vendor attributes with opaque payloads
- Unknown MISC attr subtypes are now preserved and attached to the next
  variable (no semantic guessing, payload exposed via describe_attribute)
- .attribute_payload(attr): safe textual views (ascii_escaped, hex, base64)
- .attribute_report(decoded=): list of all attrs with payload readouts
- .attribute_report_text(): human-readable text report of all hierarchy attrs
- Helper: _escape_bytes_for_report, _attr_payload_bytes
- New test: test_reader_reports_unknown_attr_payload_as_text

## 0.2.2

Attribute introspection and VCD extension output:

- .attributes(decoded=False/True): expose all parsed hierarchy attributes
- .attributes_for_handle(handle, decoded=): per-signal attribute view
- .describe_attribute(attr): decoded dict with subtype names, payloads
- .iter_vcd_extension_lines(): emit $attrbegin/$comment VCD extensions
- FstSignalMetadata: array_attributes, enum_attributes, pack_attributes,
  all_attributes sub-lists
- Enum table values/literals now unescape C-style backslash sequences
- Enhanced test_reader_attaches_sv_vhdl_metadata with decoded and
  VCD extension output assertions

## 0.2.1

Reader enhancements:

- SV/VHDL metadata from hierarchy: FstSignalMetadata attached to FstVar
  decodes SUPVAR, VALUELIST, ENUMTABLE, SOURCESTEM, SOURCEISTEM,
  COMMENT, ENVVAR, PATHNAME, and ARRAY attributes from attrbegin events
- New properties: .comments, .env_vars, .value_lists, .enum_tables,
  .source_paths, .metadata_for_handle(handle)
- Blackout semantic filtering: iter_value_changes and
  iter_time_value_pairs support respect_blackout=True to suppress
  events during dump-inactive periods
- Blackout helpers: .is_dump_active_at(time), .iter_blackout_intervals
  yielding (begin, end, active) intervals
- FstVar extended with supplemental_type_name and metadata fields
- FstAttrBegin extended with arg_from_name for SOURCESTEM varint names
- New verify/verify_reader.py tests: blackout filtering, SV/VHDL metadata

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
