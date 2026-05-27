# PurePyFstlib

Pure-Python library for reading and writing FST (Fast Signal Trace)
waveform files, with no C dependencies.

FST is the compact binary waveform format used by GTKWave and
Icarus Verilog. This library provides a platform-independent,
pure-Python implementation.

## Features

- Read FST files: headers, hierarchy, geometry, value change data
- Write FST files with zlib-compressed VCDATA sections
- Pure Python, no C extensions, no platform ABI dependencies
- Supports zlib, LZ4, and FastLZ decompression

## Quick Start

### Reading an FST file

```python
from truepyfstlib import FstReader

r = FstReader("waveform.fst")
for time, value in r.iter_value_changes(handle=1):
    print(f"{time}: {value}")
```

### Writing an FST file

```python
from truepyfstlib import FstWriter, FstScopeType, FstVarType, FstVarDir

w = FstWriter("output.fst", timescale=-9)
w.set_scope(FstScopeType.VCD_MODULE, "top")
w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "clk")
w.set_upscope()
w.emit_time_change(10)
w.emit_value_change_bit(1, 1)
w.close()
```

## License

MIT. Based on the public FST format specification from GTKWave/libfst.

