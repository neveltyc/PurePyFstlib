<p align="center">
  <h1 align="center">PurePyFstlib</h1>
  <p align="center">
    纯 Python FST（Fast Signal Trace）读写库 &mdash;
    无 C 扩展，无平台绑定，专为可移植波形调试工具链设计。
  </p>
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-0.4.0-3366cc?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10+-3366cc?style=flat-square&logo=python&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-3366cc?style=flat-square">
</p>

<p align="right"><a href="README.md">English</a></p>

---

## PurePyFstlib 是什么？

PurePyFstlib 是一个纯 Python 实现的 FST 波形格式读写库。

它面向 Python 波形调试工具链：读取真实世界里的 FST 文件，检查信号和元数据，提供 block / section / chain 级随机访问机制，并能写出一种保守、合法、GTKWave 可打开的 FST 子集，用于裁剪后的波形 artifact。

这个项目采用明确的不对称定位：

- **Reader 优先**：尽可能支持现有工具生成的常见 FST 文件；
- **Writer 保守**：只输出一种稳定的小子集，即 gzip 压缩 hierarchy + zlib 压缩 VCDATA；
- **无 C 扩展依赖**：不需要 `pylibfst`、GTKWave `libfst`、本地 C 编译或平台 wheel；
- **面向工具链**：适合作为 waveform filter、wavecut、debug reporter、Agent 辅助 RTL debug 工具的后端。

PurePyFstlib **不是** GTKWave `libfst` 的高性能仿真 dump 后端替代品。

---

## 为什么不直接使用 `pylibfst`？

需要原生 C 实现时，`pylibfst` 和 GTKWave `libfst` 仍然是正确选择。但如果目标是可移植 Python 工具链，它们会带来一些实际问题：

- 不是所有平台都有可用的预编译安装包；
- Windows 用户经常需要源码编译和本地 C 工具链；
- CI、沙盒、Agent 环境更偏好纯 Python、源码可分发的包；
- 很多 debug 工具只需要 FST 读取、裁剪、报告或小型 artifact 生成，并不需要完整仿真器级 writer。

PurePyFstlib 的价值是让 FST 更容易进入 Python、Windows、CI、Agent 和多平台波形工具链。

---

## 设计定位

PurePyFstlib 把 reader 和 writer 的职责分开。

### Reader

Reader 是本项目的主要价值。它尽量兼容常见 FST 结构，并提供稳定的底层访问能力：

```text
FST 文件
→ block 索引
→ hierarchy / geometry
→ signal table
→ VCDATA section table
→ handle 级 chain 访问
→ raw / decoded value
→ metadata 和 blackout 信息
```

这些 API 用来支撑 waveform analyzer、wavecut/filter 工具和 Agent 工作流。

### Writer

Writer 有意只输出一种保守 FST 子集：

```text
HDR + GEOM + gzip HIER + zlib VCDATA
```

这足够用于波形后处理场景：

```text
大 VCD/FST
→ 选择时间窗口
→ 选择信号子集
→ 补齐裁剪窗口边界处的初值
→ 输出小 FST
→ 直接用 GTKWave 打开，或交给后续 debug 工具处理
```

Writer 不追求复刻 `libfst` 的所有内部路径，例如 parallel writer、repack-on-close、writer-side LZ4/FastLZ 压缩、dump-size-limit 等。

---

## 安装

本地开发安装：

```bash
git clone https://github.com/neveltyc/PurePyFstlib.git
cd PurePyFstlib
pip install -e .
```

构建 wheel：

```bash
python -m pip wheel . -w dist
```

该包为纯 Python 实现，正常安装不需要 C 编译器。

---

## 快速开始

### 文件和信号检查

```python
from truepyfstlib import FstReader

with FstReader("waveform.fst") as r:
    print(r.file_info())

    for sig in r.signal_table():
        print(sig["handle"], sig["path"], sig["width"], sig["type"])
```

### 解析信号并读取时间窗口内的变化

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

### 按信号和时间随机访问

```python
from truepyfstlib import FstReader

with FstReader("waveform.fst") as r:
    handle = r.resolve_handle("top.u0.state")

    # 查询某个信号在某个时间点的值。
    print(r.get_value_at(handle, 1500, decoded=True))

    # 查询一组信号在某个时间点的 snapshot。
    handles = r.find_handles("top.u0.*")
    snap = r.snapshot_at(1500, handles=handles, decoded=True)
    print(snap)
```

### 给 analyzer / wavecut 使用的 selected event stream

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

### 读取元数据、属性和 blackout 信息

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

### 写一个保守 FST 文件

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

## 稳定 Reader API

这一节是建议外部用户依赖的 reader API。API 按职责分层，方便 waveform analyzer、wavecut 和 Agent 工具稳定集成，而不需要依赖内部解析细节。

### 文件级 getter

这些接口对应 `libfst` reader getter 的语义，但采用 Python snake_case 命名。

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

### 稳定结构表

这些接口适合 analyzer、CLI 工具和 JSON 输出。

```python
r.file_info()       # 结构化文件概览
r.block_table()     # 顶层 FST block 表
r.signal_table()    # 信号表：handle、path、aliases、width、type、metadata 摘要
r.section_table()   # VCDATA section 表：index、begin/end time、chain count、pack type
```


### 信号查找

reader 层只提供简单、明确的信号查找：精确名称、glob 或 regex。CLI 层的 substring filter、逗号规则、condition 解析，应放在 analyzer 工具里实现。

```python
r.signal_names(include_aliases=True)
r.names_for_handle(handle)

r.find_handle(name, include_aliases=True)
r.find_handles(pattern=None, regex=False, include_aliases=True, unique=True)

r.find_signal(name, include_aliases=True)
r.find_signals(pattern=None, regex=False, include_aliases=True, unique=True)

r.resolve_handle(query, regex=False, include_aliases=True)
```

推荐使用约定：

- 输入是精确名称时，用 `find_handle()`；
- 预期可能有多个结果时，用 `find_handles()` / `find_signals()`；
- 调用方必须得到唯一信号时，用 `resolve_handle()`。

### section 和时间访问

FST 的 VCDATA 是 block/section 结构。下面这些接口暴露时间随机访问机制，不强迫调用方全量扫描波形。

```python
r.vc_sections()
r.sections_overlapping(start=None, end=None)
r.section_for_time(time)
r.section_at_time(time)   # section_for_time() 的别名
```

### value 访问

这些接口面向单信号或少量信号的访问，避免解码无关 handle。

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

解码和格式化：

```python
r.decode_value(handle, value)
r.format_value(handle, value)

r.iter_decoded_value_changes(...)
r.iter_decoded_value_changes_all(...)
r.iter_decoded_value_changes_range(...)
```

### 给 analyzer / wavecut 的事件流 API

这些是 reader 提供的薄封装。它们不负责 condition 解析、协议语义、debug 报告，只负责提供高效的 selected event stream。

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

建议职责划分：

- `FstReader`：signal / section / event 访问；
- analyzer 层：condition、compare、summary、search、report；
- wavecut/filter 层：信号选择、时间窗口裁剪、输出写回。

### 元数据和属性

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

未知或第三方属性会被保留。reader 会暴露 raw bytes，以及 escaped ASCII、UTF-8/Latin-1、hex、base64 等报告友好表示，但不会猜测厂商私有业务语义。

---

## Reader 支持矩阵

| 功能 | 状态 | 说明 |
|---|---:|---|
| FST header (`HDR`) | 支持 | 包括 version、date、timescale、timezero、file type、计数字段。 |
| Geometry (`GEOM`) | 支持 | 有 GEOM 时按 GEOM 作为 frame layout 来源。 |
| 无 GEOM fallback | 支持 | 可从 hierarchy 推导 signal width/type。 |
| Hierarchy (`HIER`) | 支持 | gzip hierarchy block。 |
| LZ4 hierarchy (`HIER_LZ4`) | 支持 | 纯 Python LZ4 block 解压。 |
| LZ4DUO hierarchy (`HIER_LZ4DUO`) | 支持 | 已实现常见布局支持，仍建议增加真实语料测试。 |
| Whole-file wrapper (`ZWRAPPER`) | 支持 | 支持 gzip/raw-deflate wrapper。 |
| Static VCDATA | 支持 | 支持 frame、time table、chain table、按 handle 遍历 value changes。 |
| Dynamic alias VCDATA | 支持 | 支持 `VCDATA_DYN_ALIAS` 和 `VCDATA_DYN_ALIAS2` 解析路径。 |
| zlib chain | 支持 | 标准 zlib chain 解码。 |
| LZ4 chain | 支持 | 纯 Python raw LZ4 block 解码。 |
| FastLZ chain | 支持 | 纯 Python FastLZ 解码。 |
| Multi-section VCDATA | 支持 | 支持单 section 和跨 section iterator。 |
| Block/section random access | 支持 | section 时间索引 + handle 级 chain 访问。 |
| Scalar / vector | 支持 | 支持 raw、decoded、formatted 访问。 |
| String (`GEN_STRING`) | 支持 | variable-length payload 以 bytes 形式暴露。 |
| Real | 支持 | 支持 raw bytes 和 Python float 解码。 |
| Alias | 支持 | 一个 handle 可对应多个 hierarchy name。 |
| Blackout | 支持 | 支持 raw interval，也支持 iterator 按 blackout 语义过滤。 |
| SV/VHDL supplemental metadata | 支持 | 常见 libfst-style attribute 会解析并挂到变量上。 |
| 未知/第三方 attribute | 支持 payload 报告 | raw bytes 不丢，并提供 escaped ASCII、UTF-8/Latin-1、hex、base64 表示。 |
| VCD extension hierarchy lines | 诊断支持 | `iter_vcd_extension_lines()` 可输出 hierarchy extension 文本，但不是完整 VCD exporter。 |
| Parallel `.hier` side-file | 不支持 | 当前明确不支持。 |

---

## Writer 支持矩阵

| 功能 | 状态 | 说明 |
|---|---:|---|
| FST header (`HDR`) | 支持 | 保守 header 生成。 |
| Geometry (`GEOM`) | 支持 | 支持 fixed-width、string、real geometry。 |
| Hierarchy (`HIER`) | 支持 | 输出 gzip hierarchy。 |
| VCDATA | 支持 | 输出 zlib-packed conservative VCDATA。 |
| Scalar / vector | 支持 | 支持 1-bit 和 N-bit ASCII bit value，包括常见 x/z 等符号。 |
| String | 支持 | 支持 `GEN_STRING` variable-length payload。 |
| Real | 支持 | 支持 double payload。 |
| Alias | 支持 | 按 libfst 风格处理 alias。 |
| Multi-section output | 支持 | 可通过 `flush_context()` 生成多 section。 |
| Empty/no-change 文件 | 支持 | 可输出 frame-only section。 |
| Blackout | 支持 | `emit_dump_active()` 可写 blackout record。 |
| 常见 metadata helper | 支持 | 支持 comment、env var、value list、source stem、enum table ref、supplemental var 等。 |
| Writer-side FastLZ | 不支持 | Reader 可解码 FastLZ；writer 有意固定输出 zlib。 |
| Writer-side LZ4 | 不支持 | Reader 可解码 LZ4；writer 有意固定输出 zlib。 |
| HIER_LZ4 writer | 不支持 | writer 固定输出 gzip hierarchy。 |
| Parallel writer | 不支持 | Python writer 不作为高性能仿真 dump 后端。 |
| Repack-on-close / dump-size-limit | 不支持 | 这些属于 libfst 的性能/打包路径，当前不实现。 |

---

## 不支持 / 有意不做的内容

PurePyFstlib 不追求复刻 GTKWave `libfst` 的所有内部路径。

当前明确不支持或有意不做：

- 替代 `libfst` 作为高性能仿真器 dump writer；
- writer-side FastLZ/LZ4 压缩；
- writer-side LZ4 hierarchy；
- parallel writer 和 parallel `.hier` side-file；
- repack-on-close 和 dump-size-limit 工作流；
- 完整 `fst2vcd` 替代；
- 对每一种未知厂商私有 attribute 做业务语义解释。

对于未知或第三方 attribute，reader 的策略是：**完整保留并报告 payload，而不是猜测厂商私有语义**。

---

## 典型使用场景

- 在 Python 中读取 FST 文件，而不依赖 C 扩展；
- 给 Agent 辅助波形 debug 工具提供 signal/time/value 查询能力；
- 从大波形中裁剪特定时间窗口和信号集合；
- 将裁剪后的波形重新封装成 GTKWave 可打开的小 FST；
- 生成适合 bug report、CI artifact、跨平台 debug 会话的小型波形文件。

---

## 项目结构

```text
src/truepyfstlib/
  common.py        FST enum、dataclass 和公共结构
  compression.py   纯 Python LZ4/FastLZ 解压辅助
  reader.py        FST reader、metadata、随机访问 iterator、report API
  varint.py        FST varint 编解码
  writer.py        保守 FST writer

verify/
  roundtrip.py        小型 reader/writer roundtrip 检查
  verify_reader.py    reader 兼容性检查
  verify_writer.py    writer 检查；如果系统有 fst2vcd，会做外部验证
  verify_golden.py    golden fixture 验证辅助
```

---

## Tests

运行纯 Python 检查：

```bash
# Linux / macOS
PYTHONPATH=src python verify/roundtrip.py
PYTHONPATH=src python verify/verify_reader.py
PYTHONPATH=src python verify/verify_writer.py

# Windows (PowerShell)
$env:PYTHONPATH = "src"; python verify/roundtrip.py; python verify/verify_reader.py; python verify/verify_writer.py
```

Golden fixture 交叉验证：

```bash
PYTHONPATH=src python verify/verify_golden.py
```

如果本机安装了 GTKWave 工具链，`verify_writer.py` 可以进一步用 `fst2vcd` 验证 writer 生成的 FST 是否能被外部工具接受。发布 writer 相关版本前建议跑这一步。

构建检查：

```bash
python -m pip wheel . -w dist
python -c "import truepyfstlib; print(truepyfstlib.__version__)"
```

---

## 版本历史

详见 [`CHANGELOG.md`](CHANGELOG.md)。

---

## 许可

MIT。

本项目是面向可移植 FST 工具链的纯 Python 实现，安装时不需要链接 `pylibfst` 或 GTKWave `libfst`。
