# Changelog

## 0.0.3

- Fix 5 writer bugs causing fst2vcd hang:
  - HIER block now uses gzip (libfst gzdopen requires gzip format)
  - memory_required_for_traversal correctly computed
  - Chain table no longer emits explicit sentinel
  - write_varint byte order fixed for values >= 128
  - x/z/h/u/w/l encoding fixed (removed spurious bit)
- Fix header layout: scope_count/var_count/max_handle before timescale

## 0.0.2

- Fix DYN_ALIAS2 chain parsing: use LEB128 signed varint instead of zigzag
- Fix VC chain time deltas: accumulate instead of using absolute indices
- Fix duplicate initial value for signals with chain data
- Reorganize: tests/ -> verify/, test_fixtures/ -> verify/fixtures/

## 0.0.1

- Initial release
- FST reader: header, hierarchy, geometry, VCDATA value-change traversal
- FST writer: zlib-compressed single-section output
- Pure Python LZ4 and FastLZ decompression
- ZWRAPPER (gzip-wrapped FST file) support
- Roundtrip verification tests
