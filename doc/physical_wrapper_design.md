# physical_wrapper_gen.py — 设计规格文档

> Layer 1 (physical_wrapper) 的 Verilog 生成器。4 个 Generator class 覆盖 9 种 interface_type。
> 采用 Jinja2 模板渲染，Python 侧构建 context dict，单一模板 `physical_wrapper.v.j2` 统一输出。

---

## 目录

- [1. 架构概览](#1-架构概览)
- [2. 角色定义与设计前提](#2-角色定义与设计前提)
- [3. 输入数据结构](#3-输入数据结构)
- [4. 类架构](#4-类架构)
- [5. 基类静态方法](#5-基类静态方法)
- [6. Context Dict 完整结构](#6-context-dict-完整结构)
- [7. Jinja2 模板结构](#7-jinja2-模板结构)
- [8. SIM 行为模型](#8-sim-行为模型)
- [9. 各 Generator 生成规则](#9-各-generator-生成规则)
- [10. Dispatch 机制](#10-dispatch-机制)
- [11. Verilog 语法细节](#11-verilog-语法细节)
- [12. 风险与不支持场景](#12-风险与不支持场景)

---

## 1. 架构概览

```
scripts/physical_wrapper_gen.py        ← Python: 4 个 Generator class
scripts/templates/physical_wrapper.v.j2 ← Jinja2: 统一模板
scripts/templates/_macros.v.j2          ← Jinja2: 共享宏 (file_header)
```

**数据流**：
```
mem_gen.py
  → gen_physical_wrapper(mem_spec, ecc_params, tiling, interface_type)
    → GENERATORS[type].generate(...)
      → build_context(...)   → dict
      → template.render(ctx) → Verilog 字符串
```

**生成产物结构**（以 `ifdef SIM` 分支）：
```
module {name}_physical_wrapper (...);
  `ifdef SIM
    // 扁平行为模型（无 tiling）
  `else
    // 物理 tiling: localparam + wire + reg + cell 实例 + read mux
  `endif
endmodule
```

---

## 2. 角色定义与设计前提

> physical_wrapper_gen 是**结构映射器**，不是协议适配器。

### 2.1 职责

- 逻辑宽深到物理 row/col tiling 的展开
- wrapper active-HIGH 语义到 vendor pin 极性的映射
- sub_type const_ports / output_ports 的合并与连接
- 多 row 读返回路径的 row select 对齐与读 mux
- 功能等价的 SIM 扁平行为模型生成

### 2.2 非职责

- **不负责**逻辑 mask 到物理 mask 的转换或压缩（由 L2 承担）
- **不负责**支持任意 vendor read latency（仅支持 1-cycle，见 §2.3）
- **不负责** ECC 编解码行为
- **不负责**上层 memory protocol 适配（pipeline、init FSM 等）

### 2.3 设计前提（硬约束）

| 约束 | 说明 |
|------|------|
| **同步读** | vendor macro 必须为同步读（posedge 触发，非组合输出） |
| **1-cycle read latency** | 读数据在发出读请求后的下一个时钟沿返回，多 row 设计固定使用 1 级 `rd_row_sel_d` 对齐读返回。本层不负责适配 0-cycle 或 ≥2-cycle read latency |
| **规则 mask geometry** | `lib_width % lib_mask_width == 0`，mask 为规则分组。不支持非规则 mask geometry |
| **mask_pad_bits 仅用于报告** | `mask_pad_bits` 仅在 `TilingParams` 和 `report.json` 中作为诊断信息，不参与 physical wrapper 逻辑生成。最后一列的无效 pad 区 mask 由上层保证为安全值 |

---

## 3. 输入数据结构

生成器接收 5 个 frozen dataclass（全部由 `config_io.py` 解析产生）。其中 `SubTypeInfo` 在 `gen_physical_wrapper()` 内部由 `resolve_sub_type()` 自动解析。

### 3.1 MemorySpec（来自 mem_config.json）

| 字段 | 类型 | 生成器用途 |
|------|------|-----------|
| `name` | str | 模块名前缀 `{name}_physical_wrapper` |
| `type` | str | dispatch key（`"1rw"`, `"1r1wm"` 等） |
| `width` | int | 逻辑数据宽度（ECC 前） |
| `depth` | int | 逻辑深度 → `ADDR_WIDTH = clog2(depth)`，SIM 数组深度 |
| `physical.sub_type` | str | 用于 `resolve_sub_type()` 查找 `SubTypeInfo` |
| `physical.lib_name` | str | vendor cell 模块名 |
| `physical.lib_width` | int | 单个 cell 数据宽度 |
| `physical.lib_depth` | int | 单个 cell 深度 |
| `physical.lib_mask_width` | int | 单个 cell mask 宽度（0 = 无 mask） |

### 3.2 InterfaceType（来自 vendor_port_map.json）

| 字段 | 类型 | 说明 |
|------|------|------|
| `base_type` | str | `"single_port"` / `"dual_port"` / `"true_dual_port"` / `"rom"` |
| `has_mask` | bool | wrapper 接口是否暴露 mask 端口 |
| `is_async` | bool | dual_port 专用：true = 双时钟 |
| `port_map` | dict | logical_name → `"~?VENDOR_PIN"` 功能端口映射 |
| `sub_types` | tuple | `SubTypeInfo` 数组 |

各 interface_type 的 port_map key 一览：

| interface_type | port_map keys |
|---------------|---------------|
| 1rw / 1rwm | `clk`, `cen`, `wen`, `addr`, `wdata`, `rdata`, (+`bwen` if mask) |
| 1r1w / 1r1wm | `clk`, `wr_en`, `wr_addr`, `wr_data`, `rd_en`, `rd_addr`, `rd_data`, (+`wr_mask` if mask) |
| 1r1wa / 1r1wma | `wr_clk`, `rd_clk`, `wr_en`, `wr_addr`, `wr_data`, `rd_en`, `rd_addr`, `rd_data`, (+`wr_mask` if mask) |
| 2rw / 2rwm | `a_clk`, `a_cen`, `a_wen`, `a_addr`, `a_wdata`, `a_rdata`, `b_clk`, `b_cen`, `b_wen`, `b_addr`, `b_wdata`, `b_rdata`, (+`a_bwen`, `b_bwen` if mask) |
| rom | `clk`, `cen`, `addr`, `rdata` |

### 3.3 SubTypeInfo（来自 vendor_port_map.json → sub_types[]）

| 字段 | 类型 | 说明 |
|------|------|------|
| `names` | tuple[str] | vendor cell 家族名列表（如 `["1prf", "uhd1prf"]`） |
| `const_ports` | dict | PIN → 常量值（int / str），tie-off 到固定电平 |
| `output_ports` | tuple[str] | 悬空的 vendor 输出端口名 |

### 3.4 EccParams

生成器仅使用 `logical_total_width`（ECC 编码后的总数据宽度）作为 `DATA_WIDTH`。

### 3.5 TilingParams

| 字段 | 说明 |
|------|------|
| `col_count` | 列拼接数 = `ceil(logical_width / lib_width)` |
| `row_count` | 行拼接数 = `ceil(depth / lib_depth)` |
| `width_pad_bits` | 最后一列的高位补零数 |
| `mask_pad_bits` | 最后一列的 mask 补零数 |
| `total_mask_width` | `col_count × lib_mask_width`（mask 端口总宽度） |
| `total_blocks` | `col_count × row_count` |

---

## 4. 类架构

```
PhysicalWrapperGenerator (ABC)
│
│  静态方法:
│  ├── calc_tiling(logical_width, depth, lib_width, lib_depth, lib_mask_width) → TilingParams
│  ├── _format_const_value(value, lib_width) → str
│  ├── _build_cell_ports(func_ports, sub_type_info, lib_width) → list[str]
│  ├── _data_slice_expr(col, col_count, lib_width, data_width, width_pad_bits, data_signal) → str
│  ├── _mask_slice_expr(col, col_count, lib_mask_width, total_mask_width, mask_signal) → str
│  └── _base_context(mem_spec, ecc_params, tiling) → dict
│
│  抽象方法:
│  └── build_context(mem_spec, ecc_params, tiling, interface_type, sub_type_info) → dict
│
│  最终方法:
│  └── generate(mem_spec, ecc_params, tiling, interface_type, sub_type_info) → str
│
├── SinglePortGenerator      — 1rw, 1rwm
├── DualPortGenerator        — 1r1w, 1r1wm, 1r1wa, 1r1wma
├── TrueDualPortGenerator    — 2rw, 2rwm
└── RomGenerator             — rom
```

`generate()` 的签名：

```python
def generate(self, mem_spec: MemorySpec, ecc_params: EccParams,
             tiling: TilingParams, interface_type: InterfaceType,
             sub_type_info: SubTypeInfo) -> str:
    ctx = self.build_context(mem_spec, ecc_params, tiling, interface_type, sub_type_info)
    return _physical_wrapper_tmpl.render(ctx)
```

---

## 5. 基类静态方法

### 5.1 `calc_tiling(logical_width, depth, lib_width, lib_depth, lib_mask_width) → TilingParams`

纯计算函数，计算 col/row 拼接参数及 padding。

```python
col_count = ceil(logical_width / lib_width)
row_count = ceil(depth / lib_depth)
width_pad_bits = col_count * lib_width - logical_width
total_mask_width = col_count * lib_mask_width  # 0 if no mask
```

### 5.2 `_format_const_value(value, lib_width) → str`

将 JSON 中的常量值转为 Verilog 字面量：

| 输入 value | 输出 Verilog | 处理方式 |
|-----------|-------------|---------|
| `"zeros"` | `{32{1'b0}}` | 变宽展开（lib_width=32） |
| `"ones"` | `{32{1'b1}}` | 变宽展开（lib_width=32） |
| `0` (int) | `0` | `str(value)` |
| `1` (int) | `1` | `str(value)` |
| `"2'b11"` | `2'b11` | 直接输出 |
| `"{2'b10}"` | `{2'b10}` | 直接输出 |

> `"zeros"`/`"ones"` 展开宽度 = `lib_width`，适用于 TSMC bit-level masking（BWEB 宽度 = lib_width）。

### 5.3 `_build_cell_ports(func_ports, sub_type_info, lib_width) → list[str]`

合并功能端口、常量端口、悬空输出端口为完整列表：

```
1. func_ports:     [".CLK (clk)", ".CEB (~(cen))", ...]
2. const_lines:    [".BIST (0)", ".RTSEL ({2'b10})", ...]  ← const_ports 遍历
3. output_lines:   [".SOC ()", ".SOD ()", ...]              ← output_ports 遍历
→ 返回: func_ports + const_lines + output_lines
```

返回 `list[str]`，模板侧用 `{{ cell.ports | join(",\n    ") }}` 拼接。

### 5.4 `_data_slice_expr(col, ..., data_signal) → str`

生成写数据切片表达式：

- 非末列：`wdata[63:32]`
- 末列无 pad：`wdata[31:0]`
- 末列有 pad：`{16'b0, wdata[47:32]}`（高位补零）

### 5.5 `_mask_slice_expr(col, ..., mask_signal) → str`

直接按 `col × lib_mask_width` 切片，无 pad 处理。

### 5.6 `_base_context(mem_spec, ecc_params, tiling) → dict`

构建所有 Generator 共享的标量字段：

```python
{
    "module_name":     "{name}_physical_wrapper",
    "description":     "Physical wrapper for {type} memory",
    "date":            "YYYY-MM-DD",
    "data_width":      int,   # ECC 后的逻辑总宽
    "addr_width":      int,   # clog2(depth)
    "lib_name":        str,   # vendor cell 模块名
    "lib_addr_width":  int,   # clog2(lib_depth)
    "lib_width":       int,   # 单 cell 数据宽度
    "col_count":       int,
    "row_count":       int,
    "row_sel_width":   int,   # clog2(row_count) 或 0
    "total_phy_width": int,   # col_count × lib_width
    "width_pad_bits":  int,
    "total_mask_width": int,
    "sim_depth":       int,   # = mem_spec.depth（逻辑深度）
}
```

---

## 6. Context Dict 完整结构

每个 Generator 的 `build_context()` 调用 `_base_context()` 获取共享字段，再 `ctx.update()` 添加类型特有字段。最终 dict 包含以下所有 key：

### 6.1 共享标量字段（来自 `_base_context`）

见 §5.6。

### 6.2 模块结构字段（由各 Generator 构建）

| Key | 类型 | 说明 |
|-----|------|------|
| `module_ports` | list[str] | 预格式化端口声明，如 `"input  [ADDR_WIDTH-1:0]        addr"` |
| `internal_wires` | list[str] | wire 声明，如 `"wire [LIB_ADDR_WIDTH-1:0] lib_addr = addr[LIB_ADDR_WIDTH-1:0];"` |
| `row_sel_regs` | list[dict] | 读返回对齐状态寄存器 `{clk, input, name, enable}`，仅在有效读请求时更新（见 §2.3）；row_count==1 时为空列表 |
| `rd_data_wire_groups` | list[dict] | 读数据 wire 组 `{prefix}`；TDP 有两组 `"a_"`, `"b_"` |
| `cells` | list[dict] | 实例连接 `{inst_name, ports: list[str]}` |
| `read_muxes` | list[dict] | 读 mux `{sel_signal, wire_prefix, rd_data_name}` |

### 6.3 SIM 行为模型字段

| Key | 类型 | 说明 |
|-----|------|------|
| `sim_depth` | int | `mem_spec.depth`，SIM 数组大小 |
| `sim_mode` | str | `"rw"` / `"dp"` / `"tdp"` / `"rom"` |
| `sim_has_mask` | bool | 是否生成 masked write 逻辑 |
| `sim_mask_gran` | int | mask 粒度 = `lib_width / lib_mask_width`（无 mask 时为 1） |
| `sim_is_async` | bool | dual_port 是否使用双时钟（仅 `sim_mode=="dp"` 时有意义） |

### 6.4 各 Generator 提供的值

| Generator | sim_mode | sim_is_async | read_muxes 数量 | rd_data_wire_groups |
|-----------|----------|-------------|----------------|---------------------|
| SinglePort | `"rw"` | `False` | 1 | `[{"prefix":""}]` |
| DualPort | `"dp"` | `is_async` | 1 | `[{"prefix":""}]` |
| TrueDualPort | `"tdp"` | `False` | 2 (A, B) | `[{"prefix":"a_"}, {"prefix":"b_"}]` |
| Rom | `"rom"` | `False` | 1 | `[{"prefix":""}]` |

---

## 7. Jinja2 模板结构

### 7.1 环境配置

```python
jinja2.Environment(
    loader=FileSystemLoader("scripts/templates/"),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)
```

- `StrictUndefined`: 引用未提供的变量时立即报错
- `trim_blocks` + `lstrip_blocks`: 控制块标签不引入额外空行/空格

### 7.2 模板文件 `physical_wrapper.v.j2`

```
① file_header (import from _macros.v.j2)
② module 声明 + parameter + 端口列表
③ `ifdef SIM
     扁平行为模型 (按 sim_mode 分支)
   `else
     ④ localparam (LIB_ADDR_WIDTH, ROW_SEL_WIDTH)
     ⑤ internal_wires (row_sel, lib_addr)
     ⑥ row_sel_regs (读返回对齐状态寄存器，带 enable 条件，仅 row_count > 1)
     ⑦ rd_data wire 声明 (row_0_rd_data, ...)
     ⑧ cell 实例循环 (row × col)
     ⑨ read data mux / assign
   `endif
⑩ endmodule
```

**关键设计**：④~⑨ 全部在 `` `else `` 块内。SIM 模式不依赖任何 tiling 声明。

### 7.3 共享宏 `_macros.v.j2`

```jinja2
{% macro file_header(module_name, description, date) %}
// =============================================================================
// Module : {{ module_name }}
// Description : {{ description }}
// Generated by : sram_mem_gen (mem_gen.py)
// Date : {{ date }}
// =============================================================================
{% endmacro %}
```

---

## 8. SIM 行为模型

### 8.1 设计原则

- **扁平模型**：单一 `reg [DATA_WIDTH-1:0] sim_mem [0:sim_depth-1]`，不复制物理分块
- **与 tiling 完全解耦**：无论 col/row 如何配置，SIM 行为一致
- **直接使用 wrapper 端口信号**：`addr`、`wdata`、`rdata` 等，无中间变量

### 8.2 模板分支（sim_mode）

#### `"rw"` — SinglePort (1rw / 1rwm)

```verilog
reg [DATA_WIDTH-1:0] sim_rdata;

always @(posedge clk) begin
    if (cen) begin
        if (wen) begin
            // write (flat or masked)
        end else begin
            sim_rdata <= sim_mem[addr];
        end
    end
end
assign rdata = sim_rdata;
```

#### `"dp"` — DualPort (1r1w / 1r1wm / 1r1wa / 1r1wma)

```verilog
reg [DATA_WIDTH-1:0] sim_rdata;

always @(posedge wr_clk/clk) begin     // sim_is_async 决定时钟
    if (wr_en) begin
        // write (flat or masked)
    end
end

always @(posedge rd_clk/clk) begin
    if (rd_en) begin
        sim_rdata <= sim_mem[rd_addr];
    end
end
assign rd_data = sim_rdata;
```

#### `"tdp"` — TrueDualPort (2rw / 2rwm)

```verilog
reg [DATA_WIDTH-1:0] sim_a_rdata;
reg [DATA_WIDTH-1:0] sim_b_rdata;

always @(posedge a_clk) begin
    if (a_cen) begin
        if (a_wen) begin /* write */ end
        else begin sim_a_rdata <= sim_mem[a_addr]; end
    end
end

always @(posedge b_clk) begin
    if (b_cen) begin
        if (b_wen) begin /* write */ end
        else begin sim_b_rdata <= sim_mem[b_addr]; end
    end
end
assign a_rdata = sim_a_rdata;
assign b_rdata = sim_b_rdata;
```

#### `"rom"` — Rom

```verilog
reg [DATA_WIDTH-1:0] sim_rdata;

always @(posedge clk) begin
    if (cen) begin
        sim_rdata <= sim_mem[addr];
    end
end
assign rdata = sim_rdata;
```

### 8.3 Masked Write 逻辑

当 `sim_has_mask == True` 时，写操作替换为逐位判断：

```verilog
begin : sim_masked_write
    integer i;
    for (i = 0; i < DATA_WIDTH; i = i + 1) begin
        if (mask_signal[i / MASK_GRAN])
            sim_mem[addr][i] <= wdata[i];
    end
end
```

| sim_mode | mask_signal | 说明 |
|----------|------------|------|
| `"rw"` | `bwen` | SinglePort mask |
| `"dp"` | `wr_mask` | DualPort mask |
| `"tdp"` A | `a_bwen` | TDP A 端口 mask |
| `"tdp"` B | `b_bwen` | TDP B 端口 mask |

**`MASK_GRAN`** = `sim_mask_gran` = `lib_width / lib_mask_width`。常见值：
- bit-level mask (32/32): `MASK_GRAN = 1`，`mask[i/1]` 即 `mask[i]`
- byte-level mask (32/4): `MASK_GRAN = 8`，每 8 位共享 1 个 mask bit

### 8.4 信号名映射

SIM 模板中的信号名固定来自 wrapper 端口，不需要额外 context 字段：

| sim_mode | 写时钟 | 读时钟 | 写使能 | 写地址 | 写数据 | 读使能 | 读地址 | 读数据赋值 |
|----------|--------|--------|--------|--------|--------|--------|--------|-----------|
| `rw` | clk | clk | wen | addr | wdata | !wen | addr | rdata |
| `dp` sync | clk | clk | wr_en | wr_addr | wr_data | rd_en | rd_addr | rd_data |
| `dp` async | wr_clk | rd_clk | wr_en | wr_addr | wr_data | rd_en | rd_addr | rd_data |
| `tdp` A | a_clk | a_clk | a_wen | a_addr | a_wdata | !a_wen | a_addr | a_rdata |
| `tdp` B | b_clk | b_clk | b_wen | b_addr | b_wdata | !b_wen | b_addr | b_rdata |
| `rom` | — | clk | — | — | — | cen | addr | rdata |

---

## 9. 各 Generator 生成规则

### 9.1 SinglePortGenerator（1rw / 1rwm）

#### 模块端口

```verilog
module {name}_physical_wrapper #(
    parameter DATA_WIDTH = {data_width},
    parameter ADDR_WIDTH = {addr_width}
)(
    input                           clk,
    input                           cen,
    input                           wen,
    input  [ADDR_WIDTH-1:0]         addr,
    input  [DATA_WIDTH-1:0]         wdata,
    input  [{total_mask_width}-1:0] bwen,     // 仅 1rwm
    output [DATA_WIDTH-1:0]         rdata
);
```

#### 内部信号

| row_count | 声明 |
|-----------|------|
| == 1 | `wire [LIB_ADDR_WIDTH-1:0] lib_addr = addr[LIB_ADDR_WIDTH-1:0];` |
| > 1 | 额外 `wire [ROW_SEL_WIDTH-1:0] row_sel = addr[ADDR_WIDTH-1 -: ROW_SEL_WIDTH];` + `reg rd_row_sel_d`（仅在 `cen & ~wen` 时更新） |

#### Cell 功能端口

```python
func_ports = [
    pin_connect(port_map['clk'],   "clk"),
    pin_connect(port_map['cen'],   cen_expr),      # row-gated
    pin_connect(port_map['wen'],   "wen"),           # NOT row-gated
    pin_connect(port_map['addr'],  "lib_addr"),
    pin_connect(port_map['wdata'], data_slice_expr),
    pin_connect(port_map['rdata'], rd_data_wire),
]
if has_mask:
    func_ports.append(pin_connect(port_map['bwen'], mask_slice_expr))
```

**cen_expr**：
- `row_count == 1`: `"cen"`
- `row_count > 1`: `f"cen & (row_sel == {w}'d{row})"`

**wen 不做 row gate**：CEB 已禁用非选中行，WEB 无需额外门控。

### 9.2 DualPortGenerator（1r1w / 1r1wm / 1r1wa / 1r1wma）

内部通过 `is_async` 和 `has_mask` 两个 bool 分支，覆盖 4 种类型。

#### 模块端口

```verilog
    // is_async=true:  input wr_clk, input rd_clk
    // is_async=false: input clk
    input                           wr_en,
    input  [ADDR_WIDTH-1:0]         wr_addr,
    input  [DATA_WIDTH-1:0]         wr_data,
    input  [{total_mask_width}-1:0] wr_mask,  // 仅 1r1wm / 1r1wma
    input                           rd_en,
    input  [ADDR_WIDTH-1:0]         rd_addr,
    output [DATA_WIDTH-1:0]         rd_data
```

#### 内部信号（row_count > 1）

```verilog
wire [ROW_SEL_WIDTH-1:0]  wr_row_sel  = wr_addr[ADDR_WIDTH-1 -: ROW_SEL_WIDTH];
wire [ROW_SEL_WIDTH-1:0]  rd_row_sel  = rd_addr[ADDR_WIDTH-1 -: ROW_SEL_WIDTH];
wire [LIB_ADDR_WIDTH-1:0] wr_lib_addr = wr_addr[LIB_ADDR_WIDTH-1:0];
wire [LIB_ADDR_WIDTH-1:0] rd_lib_addr = rd_addr[LIB_ADDR_WIDTH-1:0];
// rd_row_sel_d 时钟：is_async ? rd_clk : clk（仅在 rd_en 有效时更新）
```

#### is_async 分支点（仅 2 处）

| 位置 | sync | async |
|------|------|-------|
| 模块端口 | `input clk` | `input wr_clk, input rd_clk` |
| cell 例化时钟 | `pin_connect(port_map['clk'], "clk")` | `pin_connect(port_map['wr_clk'], "wr_clk")` + `pin_connect(port_map['rd_clk'], "rd_clk")` |

`rd_row_sel_d` 时钟差异通过 `row_sel_regs` 的 `clk` 字段自然传递，`enable` 固定为 `rd_en`：
- sync: `{"clk": "clk", "input": "rd_row_sel", "name": "rd_row_sel_d", "enable": "rd_en"}`
- async: `{"clk": "rd_clk", "input": "rd_row_sel", "name": "rd_row_sel_d", "enable": "rd_en"}`

#### Cell 功能端口

```python
# 时钟（is_async 分支）
if is_async:
    func_ports.append(pin_connect(port_map['wr_clk'], "wr_clk"))
    func_ports.append(pin_connect(port_map['rd_clk'], "rd_clk"))
else:
    func_ports.append(pin_connect(port_map['clk'], "clk"))

# 写端口
func_ports.append(pin_connect(port_map['wr_en'],   wr_en_expr))   # row-gated
func_ports.append(pin_connect(port_map['wr_addr'], "wr_lib_addr"))
func_ports.append(pin_connect(port_map['wr_data'], data_slice_expr))

# 读端口
func_ports.append(pin_connect(port_map['rd_en'],   rd_en_expr))   # row-gated
func_ports.append(pin_connect(port_map['rd_addr'], "rd_lib_addr"))
func_ports.append(pin_connect(port_map['rd_data'], rd_data_wire))

# mask
if has_mask:
    func_ports.append(pin_connect(port_map['wr_mask'], mask_slice_expr))
```

**wr_en_expr / rd_en_expr**：
- `row_count == 1`: `"wr_en"` / `"rd_en"`
- `row_count > 1`: `f"wr_en & (wr_row_sel == {w}'d{row})"` / `f"rd_en & (rd_row_sel == {w}'d{row})"`

> dual_port 没有独立 chip-enable，`wr_en`/`rd_en` 同时承担使能和行选功能。

### 9.3 TrueDualPortGenerator（2rw / 2rwm）

A/B 两个端口完全对称，各自独立读写。每个 cell 同时连接 A 和 B 两组端口。

#### 模块端口

```verilog
    // Port A
    input  a_clk, a_cen, a_wen,
    input  [ADDR_WIDTH-1:0] a_addr,
    input  [DATA_WIDTH-1:0] a_wdata,
    input  [{total_mask_width}-1:0] a_bwen,   // 仅 2rwm
    output [DATA_WIDTH-1:0] a_rdata,
    // Port B (同构)
    input  b_clk, b_cen, b_wen,
    input  [ADDR_WIDTH-1:0] b_addr,
    input  [DATA_WIDTH-1:0] b_wdata,
    input  [{total_mask_width}-1:0] b_bwen,   // 仅 2rwm
    output [DATA_WIDTH-1:0] b_rdata
```

#### 内部信号（row_count > 1）

A/B 各自独立一套 row_sel、lib_addr、rd_row_sel_d：

```verilog
wire a_row_sel  = a_addr[...];  wire a_lib_addr = a_addr[...];
wire b_row_sel  = b_addr[...];  wire b_lib_addr = b_addr[...];

reg a_rd_row_sel_d;  // posedge a_clk, enable: a_cen & ~a_wen
reg b_rd_row_sel_d;  // posedge b_clk, enable: b_cen & ~b_wen
```

两套 rd_data wire: `a_row_0_rd_data`, `b_row_0_rd_data`, ...

#### Cell 功能端口

```python
# Port A
func_ports.append(pin_connect(port_map['a_clk'],   "a_clk"))
func_ports.append(pin_connect(port_map['a_cen'],   a_cen_expr))  # row-gated
func_ports.append(pin_connect(port_map['a_wen'],   "a_wen"))      # NOT row-gated
func_ports.append(pin_connect(port_map['a_addr'],  "a_lib_addr"))
func_ports.append(pin_connect(port_map['a_wdata'], a_data_slice))
func_ports.append(pin_connect(port_map['a_rdata'], a_rd_wire))
if has_mask:
    func_ports.append(pin_connect(port_map['a_bwen'], a_mask_slice))

# Port B (同构)
...
```

**a_cen_expr / b_cen_expr**：与 SinglePort 的 cen 相同模式。
**a_wen / b_wen 不做 row gate**：与 SinglePort 同理。

#### 两套 Read Mux

```python
read_muxes = [
    {"sel_signal": "a_rd_row_sel_d", "wire_prefix": "a_", "rd_data_name": "a_rdata"},
    {"sel_signal": "b_rd_row_sel_d", "wire_prefix": "b_", "rd_data_name": "b_rdata"},
]
```

### 9.4 RomGenerator（rom）

最简单的类型，无写端口、无 mask。

#### 模块端口

```verilog
    input                   clk,
    input                   cen,
    input  [ADDR_WIDTH-1:0] addr,
    output [DATA_WIDTH-1:0] rdata
```

#### Cell 功能端口

```python
func_ports = [
    pin_connect(port_map['clk'],   "clk"),
    pin_connect(port_map['cen'],   cen_expr),    # row-gated
    pin_connect(port_map['addr'],  "lib_addr"),
    pin_connect(port_map['rdata'], rd_data_wire),
]
```

内部信号与 SinglePort 结构相同，但无 wen / wdata / mask 相关信号。

---

## 10. Dispatch 机制

### 10.1 注册表

```python
GENERATORS: dict[str, PhysicalWrapperGenerator] = {
    "single_port":    SinglePortGenerator(),
    "dual_port":      DualPortGenerator(),
    "true_dual_port": TrueDualPortGenerator(),
    "rom":            RomGenerator(),
}
```

按 `interface_type.base_type` 查表（`single_port` / `dual_port` / `true_dual_port` / `rom`），而非 `mem_spec.type`（interface_type name）。
同一 Generator 实例通过 `interface_type.has_mask` / `is_async` 等 bool 字段内部分支。

### 10.2 入口函数

```python
def gen_physical_wrapper(mem_spec, ecc_params, tiling, interface_type, module_name) -> str:
    base_type = interface_type.base_type
    sub_type_info = resolve_sub_type(interface_type, mem_spec.physical.sub_type)
    generator = GENERATORS[base_type]
    return generator.generate(mem_spec, ecc_params, tiling, interface_type, sub_type_info, module_name)
```

- `resolve_sub_type()` 在入口内部调用，调用方（`mem_gen.py`）无需感知 sub_type 解析逻辑
- 外部签名不变（4 个参数），对 `mem_gen.py` 透明

---

## 11. Verilog 语法细节

### 11.1 极性处理

所有极性转换由 `pin_connect()` + `vendor_port_map.json` 的 `~` 前缀驱动。
Generator 内部只处理 active-HIGH 语义的信号表达式，不出现手动 `~(...)` 拼接。

```
wrapper 接口 (active-HIGH)          vendor cell 端口
─────────────────────────────────────────────────
cen = 1 (使能)        ──→  pin_connect("~CEB", "cen")  ──→  .CEB (~(cen))
wen = 1 (写)          ──→  pin_connect("~WEB", "wen")  ──→  .WEB (~(wen))
bwen[i] = 1 (写该bit) ──→  pin_connect("~BWEB", expr)  ──→  .BWEB (~(expr))
```

### 11.2 行拼接 enable 门控

行拼接时，只有 chip-enable 类信号做 row gate，write-enable 不做：

| 类型 | row-gated 信号 | 不 gate 的信号 | 原因 |
|------|---------------|---------------|------|
| single_port | `cen` | `wen` | CEB 已禁用非选中行 |
| dual_port | `wr_en`, `rd_en` | — | WEB/REB 同时承担 chip-enable |
| true_dual_port | `a_cen`, `b_cen` | `a_wen`, `b_wen` | 同 single_port |
| rom | `cen` | — | 无写端口 |

### 11.3 数据切片（列拼接）

`_data_slice_expr()` 生成的三种情况：

| 场景 | 表达式 | 说明 |
|------|--------|------|
| 非末列 | `wdata[63:32]` | 直接切片 |
| 末列无 pad | `wdata[31:0]` | 直接切片 |
| 末列有 pad | `{16'b0, wdata[47:32]}` | 高位补零 |

读数据方向：cell 输出连接到 `row_R_rd_data[hi:lo]`，最终由 read mux 截取 `[DATA_WIDTH-1:0]`，自动丢弃 pad 位。

### 11.4 Mask 切片

`_mask_slice_expr()` 直接按 `col × lib_mask_width` 切片，无 pad 处理。
L2 负责逻辑 mask → 物理 mask 的映射。

### 11.5 const_ports 值格式

| JSON 值 | Verilog 输出 | 处理方式 |
|---------|-------------|---------|
| `"zeros"` | `{32{1'b0}}` | 变宽展开 |
| `"ones"` | `{32{1'b1}}` | 变宽展开 |
| `0` | `0` | 直接输出 |
| `1` | `1` | 直接输出 |
| `"2'b11"` | `2'b11` | 直接输出 |
| `"{2'b10}"` | `{2'b10}` | 直接输出 |

### 11.6 output_ports 连接

悬空输出端口生成空连接：`.SOC ()`、`.PUDELAY_SD ()` 等。

### 11.7 cell 例化端口顺序

```
① 时钟（clk / wr_clk+rd_clk / a_clk+b_clk）
② 使能（cen / wr_en+rd_en / a_cen+b_cen）
③ 写使能（wen / a_wen+b_wen）— 如适用
④ 地址（addr / wr_addr+rd_addr / a_addr+b_addr）
⑤ 写数据
⑥ 读数据（output）
⑦ Mask — 如适用
⑧ const_ports（按 JSON 中的 key 顺序）
⑨ output_ports（按 JSON 中的数组顺序）
```

尾逗号由模板的 `{{ cell.ports | join(",\n    ") }}` 统一处理。

---

## 12. 风险与不支持场景

本层明确不支持以下场景。如需支持，应由新的设计扩展承担，不在当前层级堆叠分支。

| 不支持场景 | 说明 |
|-----------|------|
| 非 1-cycle read latency macro | 当前固定 1 级 `rd_row_sel_d` 对齐读返回。0-cycle（组合读）或 ≥2-cycle latency 的 vendor macro 会导致读 mux 选择信号与数据不对齐 |
| 非规则 mask geometry | 要求 `lib_width % lib_mask_width == 0`。不规则分组的 vendor mask 端口无法通过简单切片映射 |
| logical mask → physical mask remap | 本层只做 mask 的机械切片传递。逻辑 mask 宽度与物理 mask 宽度不一致时的映射、压缩由 L2 负责 |
| mask_pad_bits 的安全保证 | 最后一列 pad 区域的 mask bit 由上层（L2）保证为安全值（tie-off 或全 1），本层不做任何 clamp |
