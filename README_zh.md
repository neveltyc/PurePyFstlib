# PurePyFstlib

**PurePyFstlib** 是一个纯 Python 实现的 FST（Fast Signal Trace）波形文件 reader，以及一个保守的 FST writer。它面向的是跨平台波形调试工具、波形裁剪工具、Agent 辅助 RTL debug 工具，而不是替代 GTKWave / `libfst` 的高性能 C 语言仿真 dump 后端。

这个项目的核心目标是：在不依赖 `pylibfst` / GTKWave `libfst` C 扩展的情况下，让 Python 工具链能够读取、分析、裁剪、报告和重新封装 FST 波形文件。

当前 README 对应版本：**0.2.3**。每个版本的详细改动请看 [`CHANGELOG.md`](CHANGELOG.md)。

---

## 为什么需要这个项目

FST 相比 VCD 更紧凑，更适合传输、归档和 GTKWave 查看。在 Agent 时代，波形 debug 工具越来越需要处理较大的 VCD/FST 文件，并从中抽取特定时间窗口、特定信号集合，再生成一个小的、可以直接打开的波形 artifact。

但现有 Python FST 工具经常绑定到 C 扩展或 `pylibfst`。这会带来一些实际问题：

- `pylibfst` 并不总是在所有平台上都有可用 wheel；
- Windows 平台往往需要源码编译和本地 C 工具链；
- CI、Agent、沙盒环境更偏好纯 Python、源码可分发的包；
- 波形裁剪、报告、查询、过滤这类工具不一定需要完整 C writer 的性能路径。

所以 PurePyFstlib 采用了一个明确的不对称定位：

- **Reader 优先**：尽可能兼容真实世界里常见的 FST 文件；
- **Writer 保守**：只输出一种稳定、合法、GTKWave 可接受的 FST 子集；
- **无 C 扩展依赖**：普通 Python 包即可安装和导入；
- **面向工具链**：更适合作为 waveform filter、wavecut、debug reporter、Agent 工具的后端。

---

## 设计定位

PurePyFstlib 不是 GTKWave `libfst` 的性能替代品。

Reader 的目标是尽量支持现有工具生成的常见 FST 文件。Writer 的目标则更保守：输出 gzip 压缩的 hierarchy 和 zlib 压缩的 VCDATA。这足够用于波形裁剪、VCD/FST 过滤、生成小型 GTKWave 可读 artifact，但不追求完整复刻 `libfst` 的所有高性能 writer 路径。

更符合本项目定位的链路是：

```text
大 VCD/FST
→ 选择时间窗口
→ 选择信号子集
→ 补齐裁剪窗口起点的初始状态
→ 输出一个小 FST
→ 直接用 GTKWave 打开，或交给后续 debug 工具处理
```

---

## 安装

本地开发安装：

```bash
pip install -e .
```

构建 wheel：

```bash
python -m pip wheel . -w dist
```

该包为纯 Python 实现，正常安装不需要 C 编译器。

---

## 快速开始

### 读取 FST 文件

```python
from truepyfstlib import FstReader

r = FstReader("waveform.fst")
print(r.summary())

for var in r.vars():
    print(var.handle, var.full_name, var.length)

# 读取某个 handle 的原始 value-change bytes。
for time, value in r.iter_value_changes_all(handle=1, include_initial=True):
    print(time, value)

# 读取解码后的值：scalar/vector/real/string 会按类型转换。
for time, value in r.iter_decoded_value_changes_all(handle=1, include_initial=True):
    print(time, value)
```

### 读取属性和元数据

```python
from truepyfstlib import FstReader

r = FstReader("waveform.fst")

# reader 级别的 metadata。
print(r.comments)
print(r.env_vars)
print(r.enum_tables)
print(r.source_paths)

# 信号级 metadata。
meta = r.metadata_for_handle(1)
print(meta)

# 所有 hierarchy attribute，包括未知/第三方 payload。
for item in r.attribute_report(decoded=True):
    print(item)

# 生成可直接打印的属性报告。
print(r.attribute_report_text())
```

### 按 blackout 语义读取事件

```python
from truepyfstlib import FstReader

r = FstReader("waveform.fst")
print(r.blackouts)
print(r.is_dump_active_at(1000))

for time, value in r.iter_value_changes_all(
    handle=1,
    include_initial=True,
    respect_blackout=True,
):
    print(time, value)
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

Writer 输出策略是保守的：gzip hierarchy + zlib VCDATA。这是 Python 侧波形裁剪和重新封装时推荐使用的输出格式。

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
| Scalar / vector | 支持 | 支持 raw 和 decoded 访问。 |
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
  common.py       # FST enum、dataclass、公共结构
  compression.py  # 纯 Python LZ4/FastLZ 解压辅助
  reader.py       # FST reader、metadata、iterator、report API
  varint.py       # FST varint 编解码
  writer.py       # 保守 FST writer

verify/
  roundtrip.py        # 小型 reader/writer roundtrip 检查
  verify_reader.py    # reader 兼容性检查
  verify_writer.py    # writer 检查；如果系统有 fst2vcd，会做外部验证
  verify_golden.py    # golden fixture 验证辅助
```

---

## 验证

运行纯 Python 检查：

```bash
# Linux / macOS
PYTHONPATH=src python verify/roundtrip.py
PYTHONPATH=src python verify/verify_reader.py
PYTHONPATH=src python verify/verify_writer.py

# Windows (PowerShell)
$env:PYTHONPATH = "src"; python verify/roundtrip.py; python verify/verify_reader.py; python verify/verify_writer.py
```

Golden fixture 交叉验证（需要 GTKWave `fst2vcd`）：

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

每个版本的详细改动维护在 [`CHANGELOG.md`](CHANGELOG.md)。

如果后续 README 需要版本表，版本说明字段可以先留空，由本地 release agent 根据 `CHANGELOG.md` 自动补齐。

---

## 许可

MIT。

本项目是面向可移植 FST 工具链的纯 Python 实现，安装时不需要链接 `pylibfst` 或 GTKWave `libfst`。
