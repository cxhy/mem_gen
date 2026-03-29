# memory_wrapper_gen.py — 设计规格文档

> Layer 2 (memory_wrapper) 的 Verilog 生成器。4 个 Generator class 覆盖 9 种 interface_type。
> 采用 Jinja2 模板渲染，Python 侧构建 context dict，单一模板统一输出。

---

## 目录

- [1. 架构概览](#1-架构概览)
- [2. 角色定义与设计约束](#2-角色定义与设计约束)
- [3. 内部数据流](#3-内部数据流)
- [4. 类型兼容架构](#4-类型兼容架构)
- [5. L2 外部接口定义](#5-l2-外部接口定义)
- [6. L2 → L1 端口适配](#6-l2--l1-端口适配)
- [7. 延时模型](#7-延时模型)
- [8. Init FSM 规格](#8-init-fsm-规格)
- [9. ECC 子系统](#9-ecc-子系统)
- [10. Pipeline 规格](#10-pipeline-规格)
- [11. 配置参数](#11-配置参数)
- [12. Generator 类架构](#12-generator-类架构)

---

## 1. 架构概览

```
scripts/memory_wrapper_gen.py          ← Python: 4 个 Generator class
scripts/templates/memory_wrapper.v.j2  ← Jinja2: 统一模板
scripts/templates/_macros.v.j2         ← Jinja2: 共享宏 (复用 L1)
```

**数据流**：
```
mem_gen.py
  → gen_memory_wrapper(mem_spec, ecc_params, ecc_modules, interface_type)
    → GENERATORS[base_type].generate(...)
      → build_context(...)   → dict
      → template.render(ctx) → Verilog 字符串
```

**生成产物层次**：
```
{name}_memory_wrapper        ← L2 (本文档)
  └─ {name}_physical_wrapper ← L1 (已稳定)
       └─ N×M Vendor Memory
```

---

## 2. 角色定义与设计约束

### 2.1 职责

- 用户侧接口到 L1 物理层接口的**协议适配**
- Input / Output / ECC 三段 pipeline 管理
- ECC encode（写路径）、ECC decode + error report（读路径）
- 前门 Init FSM（地址扫描初始化）
- ECC 错误注入（`i_ecc_err_insert`）与错误屏蔽（`i_ecc_err_mask`）

### 2.2 非职责

- **不负责** tiling / vendor pin 映射（由 L1 承担）
- **不负责** 读写同地址 bypass（由 L3 承担）
- **不负责** 跨时钟域同步（async dual_port 的 CDC 由上层负责）

### 2.3 设计约束（硬约束）

| 约束 | 说明 |
|------|------|
| **Init 期间禁止正常读写** | 设计约束，用户需保证 `o_init_done=0` 期间不发起读写。L2 不做 stall 或仲裁 |
| **单时钟复位** | `i_rst_n` 为全局异步低有效复位，所有寄存器共享 |
| **ECC encode/decode 纯组合** | ECC 编解码本身不引入寄存器级。时序不满足时通过 `ecc_pipe_stages` 插入寄存器 |
| **ROM 无前门 Init** | ROM 通过 `$readmemh` 加载内容，不生成 Init FSM |

---

## 3. 内部数据流

### 3.1 写路径

```
用户写信号 → [Input Pipe] → Init FSM Mux → ECC Encode → Mask 扩展 → L1 写端口
```

- **Init FSM Mux**：init 期间接管地址和数据，正常模式透传
- **ECC Encode**：`DATA_WIDTH` → pad → 分片 encode → `DATA_WITH_ECC_DW`
- **Mask 扩展**：init 期间 mask 强制全 1；ECC 开启时，ECC 校验位对应 mask 强制为 1

### 3.2 读路径

```
L1 读数据 → [Read Latency Align] → ECC Decode → Error Inject/Mask → Output Mux
  → [ECC Pipe] → [Output Pipe] → 用户读数据
```

- **Read Latency Align**：`data_syncn` 延迟 `rd_en + rd_addr`，与读数据对齐
- **ECC Decode**：`DATA_WITH_ECC_DW` → 分片 decode → 纠正数据 + syndrome + error flags
- **Error Inject/Mask**：`(real_error | inject) & ~mask`
- **Output Mux**：`ecc_en ? corrected_data : raw_data`
- **ECC Pipe + Output Pipe**：error 信号与数据对齐，一同打拍

---

## 4. 类型兼容架构

L2 按 `base_type` 分为 4 个 Generator class，与 L1 一致：

| Generator | base_type | 覆盖类型 | 时钟 |
|-----------|-----------|---------|------|
| `SinglePortWrapperGen` | `single_port` | 1rw, 1rwm | 单时钟 |
| `DualPortWrapperGen` | `dual_port` | 1r1w, 1r1wm, 1r1wa, 1r1wma | 单/双时钟 |
| `TrueDualPortWrapperGen` | `true_dual_port` | 2rw, 2rwm | 双时钟 |
| `RomWrapperGen` | `rom` | rom | 单时钟 |

各 Generator 的分支维度：

| 维度 | SinglePort | DualPort | TrueDualPort | Rom |
|------|-----------|----------|-------------|-----|
| has_mask | ✓ | ✓ | ✓ | — |
| is_async | — | ✓ | — | — |
| ecc_en | ✓ | ✓ | ✓ | ✓ |
| has_init | ✓ | ✓ | ✓ | **✗** |

---

## 5. L2 外部接口定义

### 5.1 公共端口（所有类型）

| 端口 | 方向 | 位宽 | 说明 |
|------|------|------|------|
| `i_rst_n` | input | 1 | 异步低有效复位 |

### 5.2 ECC 公共端口（ecc_en=true 时）

| 端口 | 方向 | 位宽 | 说明 |
|------|------|------|------|
| `i_ecc_en` | input | 1 | ECC 使能（运行时可切换） |
| `i_ecc_err_insert` | input | 2 | 软件注入：`[0]`=1bit, `[1]`=2bit |
| `i_ecc_err_mask` | input | 2 | 软件屏蔽：`[0]`=mask 1bit, `[1]`=mask 2bit |
| `o_ecc_correctable_valid` | output | 1 | 可纠正错误有效 |
| `o_ecc_correctable_addr` | output | ADDR_WIDTH | 可纠正错误地址 |
| `o_ecc_uncorrectable_valid` | output | 1 | 不可纠正错误有效 |
| `o_ecc_uncorrectable_addr` | output | ADDR_WIDTH | 不可纠正错误地址 |

### 5.3 ECC 详细报告端口（ecc_en=true 且 detailed_report=true 时）

| 端口 | 方向 | 位宽 | 说明 |
|------|------|------|------|
| `o_ecc_err_syndrome` | output | M | 第一个出错 slice 的 syndrome |

### 5.4 Init 端口（base_type ≠ rom 时）

| 端口 | 方向 | 位宽 | 说明 |
|------|------|------|------|
| `i_init_en` | input | 1 | 初始化启动（高有效） |
| `i_init_value` | input | 1 | 初始化值（0 或 1，复制到全宽） |
| `o_init_done` | output | 1 | 初始化完成标志 |

### 5.5 SinglePort 类型端口（1rw / 1rwm）

| 端口 | 方向 | 位宽 | 说明 |
|------|------|------|------|
| `i_clk` | input | 1 | 时钟 |
| `i_cen` | input | 1 | 片选使能，1=使能 |
| `i_wen` | input | 1 | 写使能，1=写，0=读 |
| `i_addr` | input | ADDR_WIDTH | 地址 |
| `i_wdata` | input | DATA_WIDTH | 写数据 |
| `i_bwen` | input | MASK_WIDTH | 按位写使能（仅 1rwm，MASK_WIDTH = DATA_WIDTH / mask_gran） |
| `o_rdata` | output | DATA_WIDTH | 读数据 |

### 5.6 DualPort 类型端口（1r1w / 1r1wm / 1r1wa / 1r1wma）

**同步（is_async=false）**：

| 端口 | 方向 | 位宽 | 说明 |
|------|------|------|------|
| `i_clk` | input | 1 | 共享时钟 |

**异步（is_async=true）**：

| 端口 | 方向 | 位宽 | 说明 |
|------|------|------|------|
| `i_wr_clk` | input | 1 | 写时钟 |
| `i_rd_clk` | input | 1 | 读时钟 |

**写端口**：

| 端口 | 方向 | 位宽 | 说明 |
|------|------|------|------|
| `i_wr_en` | input | 1 | 写使能 |
| `i_wr_addr` | input | ADDR_WIDTH | 写地址 |
| `i_wr_data` | input | DATA_WIDTH | 写数据 |
| `i_wr_bwen` | input | MASK_WIDTH | 按位写使能（仅 mask 类型，MASK_WIDTH = DATA_WIDTH / mask_gran） |

**读端口**：

| 端口 | 方向 | 位宽 | 说明 |
|------|------|------|------|
| `i_rd_en` | input | 1 | 读使能 |
| `i_rd_addr` | input | ADDR_WIDTH | 读地址 |
| `o_rd_data` | output | DATA_WIDTH | 读数据 |

### 5.7 TrueDualPort 类型端口（2rw / 2rwm）

Port A 和 Port B 完全对称，各自独立读写。

| 端口 | 方向 | 位宽 | 说明 |
|------|------|------|------|
| `i_a_clk` / `i_b_clk` | input | 1 | A/B 独立时钟 |
| `i_a_cen` / `i_b_cen` | input | 1 | 片选使能 |
| `i_a_wen` / `i_b_wen` | input | 1 | 写使能 |
| `i_a_addr` / `i_b_addr` | input | ADDR_WIDTH | 地址 |
| `i_a_wdata` / `i_b_wdata` | input | DATA_WIDTH | 写数据 |
| `i_a_bwen` / `i_b_bwen` | input | MASK_WIDTH | 按位写使能（仅 2rwm，MASK_WIDTH = DATA_WIDTH / mask_gran） |
| `o_a_rdata` / `o_b_rdata` | output | DATA_WIDTH | 读数据 |

ECC error report 端口也是双份（A/B 各自独立报告）：
- `o_a_ecc_correctable_valid` / `o_b_ecc_correctable_valid`
- `o_a_ecc_correctable_addr` / `o_b_ecc_correctable_addr`
- `o_a_ecc_uncorrectable_valid` / `o_b_ecc_uncorrectable_valid`
- `o_a_ecc_uncorrectable_addr` / `o_b_ecc_uncorrectable_addr`

Init FSM 使用 **Port A** 执行初始化写入。

### 5.8 Rom 类型端口（rom）

| 端口 | 方向 | 位宽 | 说明 |
|------|------|------|------|
| `i_clk` | input | 1 | 时钟 |
| `i_cen` | input | 1 | 片选使能 |
| `i_addr` | input | ADDR_WIDTH | 地址 |
| `o_rdata` | output | DATA_WIDTH | 读数据 |

无 Init 端口，无写端口。

---

## 6. L2 → L1 端口适配

L2 对用户暴露统一语义端口，内部适配到 L1 物理层端口。

### 6.1 SinglePort（L2 → L1）

```
L1.clk   ← i_clk
L1.cen   ← pipe_cen | init_ram_en
L1.wen   ← (pipe_cen & pipe_wen) | init_ram_en
L1.addr  ← init_ram_en ? init_addr : pipe_addr
L1.wdata ← init_ram_en ? ecc_encode(init_value) : ecc_encode(pipe_wdata)
L1.bwen  ← init_ram_en ? ALL_ONES : ecc_expand(pipe_bwen)    // 仅 mask 类型
L1.rdata → physical_ram_rd_data
```

读使能隐含在 `pipe_cen & ~pipe_wen` 中。

### 6.2 DualPort（L2 → L1）

**同步**：
```
L1.clk     ← i_clk
L1.wr_en   ← pipe_wr_en | init_ram_en
L1.wr_addr ← init_ram_en ? init_addr : pipe_wr_addr
L1.wr_data ← init_ram_en ? ecc_encode(init_value) : ecc_encode(pipe_wr_data)
L1.wr_mask ← init_ram_en ? ALL_ONES : ecc_expand(pipe_wr_bwen)
L1.rd_en   ← pipe_rd_en
L1.rd_addr ← pipe_rd_addr
L1.rd_data → physical_ram_rd_data
```

**异步**：
```
L1.wr_clk  ← i_wr_clk
L1.rd_clk  ← i_rd_clk
其余同 sync
```

异步 dual_port 注意事项：
- Input Pipeline（写侧）使用 `i_wr_clk`
- Input Pipeline（读侧）使用 `i_rd_clk`
- Init FSM 使用 `i_wr_clk`（通过写端口初始化）
- Read Latency Alignment 使用 `i_rd_clk`
- Output Pipeline 使用 `i_rd_clk`

### 6.3 TrueDualPort（L2 → L1）

A/B 两个端口各自独立适配：

```
L1.a_clk   ← i_a_clk
L1.a_cen   ← pipe_a_cen | init_ram_en          // Init 使用 Port A
L1.a_wen   ← (pipe_a_cen & pipe_a_wen) | init_ram_en
L1.a_addr  ← init_ram_en ? init_addr : pipe_a_addr
L1.a_wdata ← init_ram_en ? ecc_encode(init_value) : ecc_encode(pipe_a_wdata)
L1.a_bwen  ← init_ram_en ? ALL_ONES : ecc_expand(pipe_a_bwen)
L1.a_rdata → physical_ram_a_rd_data

L1.b_clk   ← i_b_clk
L1.b_cen   ← pipe_b_cen                         // Port B 不参与 init
L1.b_wen   ← pipe_b_cen & pipe_b_wen
L1.b_addr  ← pipe_b_addr
L1.b_wdata ← ecc_encode(pipe_b_wdata)
L1.b_bwen  ← ecc_expand(pipe_b_bwen)
L1.b_rdata → physical_ram_b_rd_data
```

Init FSM 使用 `i_a_clk`。

### 6.4 Rom（L2 → L1）

```
L1.clk   ← i_clk
L1.cen   ← pipe_cen
L1.addr  ← pipe_addr
L1.rdata → physical_ram_rd_data
```

无写路径，无 Init。

---

## 7. 延时模型

### 7.1 总读延时公式

```
TOTAL_RD_LATENCY = INPUT_PIPE_STAGES + ram_rd_latency + ECC_PIPE_STAGES + OUTPUT_PIPE_STAGES
```

| 段 | 参数 | 默认值 | 说明 |
|----|------|-------|------|
| Input Pipeline | `INPUT_PIPE_STAGES` | 0 | 输入信号打拍（0=bypass） |
| SRAM Read | `ram_rd_latency` | 1 | 物理层读延时（当前 L1 固定 1-cycle，参数保留为未来 ≥2 预留） |
| ECC Pipeline | `ECC_PIPE_STAGES` | 0 | ECC decode 后打拍（0=组合直通，1=插一级寄存器优化时序） |
| Output Pipeline | `OUTPUT_PIPE_STAGES` | 0 | 最终输出打拍 |

### 7.2 各段延时位置

```
            ┌───────────┐   ┌──────────┐   ┌──────┐   ┌──────────┐   ┌───────────┐
User In ──→│ Input Pipe │──→│ Init/ECC │──→│ SRAM │──→│ ECC Dec  │──→│ ECC Pipe  │
            │ N cycles   │   │ (comb)   │   │1 cyc │   │ (comb)   │   │ P cycles  │
            └───────────┘   └──────────┘   └──────┘   └──────────┘   └─────┬─────┘
                                                                           │
            ┌────────────┐   ┌──────────┐                                  │
User Out ←──│ Output Pipe│←──│ Out Mux  │←─────────────────────────────────┘
            │ M cycles   │   │ (comb)   │
            └────────────┘   └──────────┘
```

### 7.3 信号对齐

ECC error report 信号（`o_ecc_correctable_valid/addr`、`o_ecc_uncorrectable_valid/addr`）与 `o_rd_data` 经过相同的 pipeline 级数，保证同拍到达。

Read Latency Alignment 延迟 `rd_en + rd_addr` 的拍数：
```
ALIGN_DELAY = ram_rd_latency
```

Error report 信号再经过 `ECC_PIPE_STAGES + OUTPUT_PIPE_STAGES` 级打拍后到达输出端口。

---

## 8. Init FSM 规格

### 8.1 适用范围

仅 `base_type ∈ {single_port, dual_port, true_dual_port}` 生成 Init FSM。ROM 不生成。

### 8.2 状态机行为

```
IDLE ──(init_en)──→ INIT ──(addr 扫描完成)──→ IDLE
                     │
                     └─ 每拍写入一个地址，从 0 到 RAM_DEPTH-1
```

| 信号 | 说明 |
|------|------|
| `init_started` | 状态标志：是否正在初始化 |
| `init_count` | 地址计数器（ADDR_WIDTH+1 位，计数 0 ~ RAM_DEPTH） |
| `init_done` | 完成标志：初始化完成后保持为 1，下次初始化开始时清 0 |
| `init_ram_en` | 内部写使能：`init_started && (init_count < RAM_DEPTH)` |
| `init_ram_addr` | 内部写地址：`init_count[ADDR_WIDTH-1:0]` |

### 8.3 Init 写数据

- 无 ECC：`{DATA_WIDTH{i_init_value}}`
- 有 ECC：`{DATA_PAD_WIDTH{i_init_value}}` → ECC encode → `DATA_WITH_ECC_DW` 位

### 8.4 Init 期间 Mask 处理

**关键修复**：Init 期间 mask 信号必须强制全 1（写入所有 bit）。

```verilog
wire [MASK_WIDTH-1:0] phy_bwen = init_ram_en ? {MASK_WIDTH{1'b1}} : ecc_expanded_bwen;
```

### 8.5 命名规范

所有信号统一 snake_case：`init_started`、`init_count`、`init_done`、`init_ram_en`。

---

## 9. ECC 子系统

### 9.1 ECC Encode（写路径）

```
i_wr_data[DATA_WIDTH-1:0]
  → pad: {zeros, i_wr_data} → [DATA_PAD_WIDTH-1:0]
  → genvar 分片 encode: [g_k*ECC_SLICE_DW +: ECC_SLICE_DW] → encoder → [g_k*ECC_SLICE_WITH_ECC_DW +: ECC_SLICE_WITH_ECC_DW]
  → gen_done_wr_eccdata[DATA_WITH_ECC_DW-1:0]
```

### 9.2 ECC Decode（读路径）

```
physical_ram_rd_data[DATA_WITH_ECC_DW-1:0]
  → genvar 分片 decode:
      data_i  [g_k*ECC_SLICE_WITH_ECC_DW +: ECC_SLICE_WITH_ECC_DW]
      data_o  [g_k*ECC_SLICE_DW +: ECC_SLICE_DW]          → corrected_data
      syndrome_o [g_k*M +: M]                              → syndrome（参数化，非硬编码 8）
      err_o   [g_k*2 +: 2]                                 → {2bit_err, 1bit_err}
```

### 9.3 non-ECC 数据提取

当 `i_ecc_en=0` 时，从物理层读数据中提取原始数据位（跳过 ECC 校验位）：

```verilog
// 注意：源切片步进用 ECC_SLICE_WITH_ECC_DW，不是 ECC_SLICE_DW
no_ecc_data[g_i*ECC_SLICE_DW +: ECC_SLICE_DW] =
    physical_rd_data[g_i*ECC_SLICE_WITH_ECC_DW +: ECC_SLICE_DW];
```

> 参考代码 demo_wrapper.v 中此处有 bug（源切片用了 `ECC_SLICE_DW` 步进），已修正。

### 9.4 Output Mux

```verilog
wire [DATA_WIDTH-1:0] out_rd_data = i_ecc_en
    ? corrected_data[DATA_WIDTH-1:0]
    : no_ecc_data[DATA_WIDTH-1:0];
```

### 9.5 Error Injection / Masking

端口：
- `i_ecc_err_insert[1:0]`：`[0]`=注入 1bit 可纠正错误，`[1]`=注入 2bit 不可纠正错误
- `i_ecc_err_mask[1:0]`：`[0]`=屏蔽 1bit 报告，`[1]`=屏蔽 2bit 报告

逻辑：
```verilog
o_ecc_correctable_valid   = (((|slice_1bit_err) & rd_vld) | i_ecc_err_insert[0]) & (~i_ecc_err_mask[0]);
o_ecc_uncorrectable_valid = (((|slice_2bit_err) & rd_vld) | i_ecc_err_insert[1]) & (~i_ecc_err_mask[1]);
```

`i_ecc_err_insert` **不 gate 到 `rd_vld`**：这是软件行为，注入时无论是否有读操作都报错。

### 9.6 详细 Error Report（可选）

JSON 配置 `ecc.detailed_report = true` 时，额外生成 syndrome 输出。

使用**单 always 块 + 普通 for 循环**实现优先编码器，提取第一个出错 slice 的 syndrome：

```verilog
reg [M-1:0] ecc_err_syndrome;
integer i;
always @(*) begin
    ecc_err_syndrome = {M{1'b0}};
    for (i = ECC_SLICE_NUM - 1; i >= 0; i = i - 1) begin
        if (chk_done_err[i*2 +: 2] != 2'b00)
            ecc_err_syndrome = chk_done_syndrome[i*M +: M];
    end
end
```

> 反向遍历确保最终保留的是最低编号 slice 的 syndrome（优先级最高）。

### 9.7 ECC + Mask 位宽扩展

ECC 开启时，物理层 mask 宽度为 `DATA_WITH_ECC_DW`，但用户侧 mask 为 `DATA_WIDTH`。
L2 需将用户 mask 扩展：ECC 校验位对应的 mask 强制为 1（始终写入）。

```
用户 mask [DATA_WIDTH-1:0]
  → pad: {{PAD_WIDTH{1'b1}}, user_mask} → [DATA_PAD_WIDTH-1:0]
  → 逐 slice 扩展: data mask bits + {ECC_BITS{1'b1}} → [DATA_WITH_ECC_DW-1:0]
```

---

## 10. Pipeline 规格

### 10.1 Input Pipeline

使用 `data_syncn`（`NUM_FLOPS=INPUT_PIPE_STAGES`，0=bypass）。

打拍信号按 base_type 分组：

| base_type | 打拍信号 | 时钟 |
|-----------|---------|------|
| single_port | cen, wen, addr, wdata, (bwen) | i_clk |
| dual_port sync | wr_en, wr_addr, wr_data, (wr_bwen), rd_en, rd_addr | i_clk |
| dual_port async | wr_en, wr_addr, wr_data, (wr_bwen) | i_wr_clk |
| dual_port async | rd_en, rd_addr | i_rd_clk |
| true_dual_port | a_cen, a_wen, a_addr, a_wdata, (a_bwen) | i_a_clk |
| true_dual_port | b_cen, b_wen, b_addr, b_wdata, (b_bwen) | i_b_clk |
| rom | cen, addr | i_clk |

**async dual_port 和 true_dual_port 需要两个独立的 input pipeline 实例**（各自时钟域）。

### 10.2 ECC Pipeline

使用 `data_syncn`（`NUM_FLOPS=ECC_PIPE_STAGES`，0=bypass）。

打拍信号：`out_rd_data` + error report 信号（valid, addr, syndrome）。
时钟：读时钟（sync 用 `i_clk`，async 用 `i_rd_clk`，TDP 每个端口独立）。

### 10.3 Output Pipeline

使用 `data_syncn`（`NUM_FLOPS=OUTPUT_PIPE_STAGES`，0=bypass）。

打拍信号：`rd_data` + error report 信号（valid, addr, syndrome）。
时钟：同 ECC Pipeline。

---

## 11. 配置参数

### 11.1 JSON 配置变更

`mem_config.json` 中每个 memory 实例新增/变更字段：

```jsonc
{
  "name": "example",
  "type": "1r1w",
  "width": 64,
  "depth": 512,
  "ecc": {
    "enable": true,
    "code_type": "hamming",
    "data_bits_per_slice": 102,
    "ecc_bits_per_slice": 8,
    "module_prefix": "lx",
    "seed": null,
    "detailed_report": false          // NEW: 详细 ECC 报告（含 syndrome），默认 false
  },
  "physical": { ... },
  "ram_rd_latency": 1,
  "input_pipe_stages": 0,
  "ecc_pipe_stages": 0,              // NEW: ECC decode 后打拍级数，默认 0
  "output_pipe_stages": 0,
  "enable_l2": true,
  "enable_l3": false
}
```

### 11.2 MemorySpec dataclass 变更

```python
@dataclass(frozen=True)
class MemorySpec:
    ...
    ecc_pipe_stages: int = 0          # NEW

@dataclass(frozen=True)
class EccConfig:
    ...
    detailed_report: bool = False     # NEW
```

### 11.3 Verilog parameter 列表

```verilog
module {name}_memory_wrapper #(
    parameter DATA_WIDTH            = ...,
    parameter RAM_DEPTH             = ...,
    parameter RAM_RD_LATENCY        = ...,
    // ECC parameters (ecc_en=true 时)
    parameter ECC_SLICE_DW          = ...,
    parameter ECC_SLICE_WITH_ECC_DW = ...,
    parameter ECC_SLICE_NUM         = ...,
    parameter DATA_PAD_WIDTH        = ECC_SLICE_DW * ECC_SLICE_NUM,
    parameter DATA_WITH_ECC_DW      = ECC_SLICE_WITH_ECC_DW * ECC_SLICE_NUM,
    // Pipeline parameters
    parameter INPUT_PIPE_STAGES     = ...,
    parameter ECC_PIPE_STAGES       = ...,    // NEW
    parameter OUTPUT_PIPE_STAGES    = ...,
    parameter ADDR_WIDTH            = $clog2(RAM_DEPTH)
)(...);
```

---

## 12. Generator 类架构

### 12.1 类层次

```
MemoryWrapperGenerator (ABC)
│
│  抽象方法:
│  └── build_context(mem_spec, ecc_params, ecc_modules, interface_type) → dict
│
│  最终方法:
│  └── generate(mem_spec, ecc_params, ecc_modules, interface_type) → str
│
├── SinglePortWrapperGen      — 1rw, 1rwm
├── DualPortWrapperGen        — 1r1w, 1r1wm, 1r1wa, 1r1wma
├── TrueDualPortWrapperGen    — 2rw, 2rwm
└── RomWrapperGen             — rom
```

### 12.2 Dispatch

```python
GENERATORS: dict[str, MemoryWrapperGenerator] = {
    "single_port":    SinglePortWrapperGen(),
    "dual_port":      DualPortWrapperGen(),
    "true_dual_port": TrueDualPortWrapperGen(),
    "rom":            RomWrapperGen(),
}

def gen_memory_wrapper(mem_spec, ecc_params, ecc_modules, interface_type) -> str:
    generator = GENERATORS[interface_type.base_type]
    return generator.generate(mem_spec, ecc_params, ecc_modules, interface_type)
```

### 12.3 Context dict 结构

各 Generator 的 `build_context()` 返回统一格式的 dict，模板通过字段存在性做条件渲染：

**标量字段**：
- `module_name`, `description`, `date`
- `data_width`, `addr_width`, `ram_depth`
- `has_ecc`, `has_mask`, `has_init`, `is_async`
- `ecc_*` 参数（slice_dw, slice_with_ecc_dw, slice_num, m, enc_module, dec_module）
- `input_pipe_stages`, `ecc_pipe_stages`, `output_pipe_stages`, `ram_rd_latency`
- `detailed_report`

**结构字段**：
- `module_ports`: list[str] — 端口声明
- `phy_wrapper_name`: str — L1 模块名
- `phy_inst_ports`: list[str] — L1 实例连接
- `input_pipes`: list[dict] — 输入 pipeline 配置 `{clk, signals, inst_name}`
- `init_fsm`: dict | None — Init FSM 配置
- `ecc_encode`: dict | None — ECC encode 配置
- `read_paths`: list[dict] — 读路径配置 `{clk, rd_en_signal, rd_addr_signal, rd_data_signal, prefix}`

> `read_paths` 对 TDP 有两个条目（A/B），其余类型只有一个。
