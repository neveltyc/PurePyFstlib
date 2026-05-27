# PurePyFstlib

纯 Python 实现的 FSTï¼Fast Signal Traceï¼波形文件读写库ï¼无 C 依赖。

FST 是 GTKWave 和 Icarus Verilog 使用的紧凑二进制波形格式。
本库提供跨平台、纯 Python 的实现。

## 特性

- 读取 FST 文件ï¼头部、层级结构ï¼scope/variableï¼、几何信息、信号值变化
- 写入 FST 文件ï¼支持 zlib 压缩的 VCDATA 段ï¼
- 纯 Pythonï¼无 C 扩展ï¼无平台 ABI 依赖
- 支持 zlib、LZ4、FastLZ 解压

## 安装

```bash
pip install git+https://github.com/neveltyc/PurePyFstlib.git
```

或克隆后本地安装ï¼

```bash
git clone https://github.com/neveltyc/PurePyFstlib.git
cd PurePyFstlib
pip install -e .
```

## 快速开始

### 读取 FST 文件

```python
from truepyfstlib import FstReader

r = FstReader("waveform.fst")
for time, value in r.iter_value_changes(handle=1):
    print(f"{time}: {value}")
```

### 写入 FST 文件

```python
from truepyfstlib import FstWriter, FstScopeType, FstVarType, FstVarDir

w = FstWriter("output.fst", timescale=-9)
w.set_scope(FstScopeType.VCD_MODULE, "top")
w.create_var(FstVarType.VCD_WIRE, FstVarDir.IMPLICIT, 1, "clk")
w.set_upscope()

w.emit_time_change(0)
w.emit_value_change_bit(1, 1)
w.emit_time_change(10)
w.emit_value_change_bit(1, 0)
w.close()
```

更多示例见 [verify/roundtrip.py](verify/roundtrip.py)。

## 许可

MIT。基于 GTKWave/libfst 的公开 FST 格式规范。
