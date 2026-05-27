# Changelog

## 0.0.2 (unreleased)

- Fix DYN_ALIAS2 chain parsing: use LEB128 signed varint instead of zigzag
- Fix VC chain time deltas: accumulate instead of using absolute indices
- Fix duplicate initial value for signals with chain data
- Reorganize: tests/ -> verify/, test_fixtures/ -> verify/fixtures/

## 0.0.1 (2026-05-27)

- Initial release
- FST reader: header, hierarchy, geometry, VCDATA value-change traversal
- FST writer: zlib-compressed single-section output
- Pure Python LZ4 and FastLZ decompression
- ZWRAPPER (gzip-wrapped FST file) support
- Roundtrip verification tests
