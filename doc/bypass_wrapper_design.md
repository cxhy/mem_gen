# L3 Bypass Wrapper — 详细设计规格

> Layer 3 最外层封装。解决 sync dual_port (1r1w / 1r1wm) 同周期同地址读写冲突。
> L3 对外接口与 L2 DualPort **完全一致**（透明包裹）。

---

## 目录

- [1. 适用范围与约束](#1-适用范围与约束)
- [2. 层次结构与命名](#2-层次结构与命名)
- [3. 接口规格](#3-接口规格)
  - [3.1 1r1w — Bypass Wrapper（无 Mask）](#31-1r1w--bypass-wrapper无-mask)
  - [3.2 1r1wm — Bypass Wrapper（带 Mask）](#32-1r1wm--bypass-wrapper带-mask)
- [4. 参数列表](#4-参数列表)
- [5. 微架构](#5-微架构)
  - [5.1 总体结构](#51-总体结构)
  - [5.2 冲突检测](#52-冲突检测)
  - [5.3 Bypass Entry 打包](#53-bypass-entry-打包)
  - [5.4 延迟对齐 Pipeline](#54-延迟对齐-pipeline)
  - [5.5 输出 Mux](#55-输出-mux)
  - [5.6 ECC 信号处理](#56-ecc-信号处理)
  - [5.7 直通信号](#57-直通信号)
- [6. 时序分析](#6-时序分析)
  - [6.1 延迟模型](#61-延迟模型)
  - [6.2 为什么单条目 Bypass 足够](#62-为什么单条目-bypass-足够)
- [7. 时序图](#7-时序图)
  - [7.1 1r1w — 冲突 Bypass](#71-1r1w--冲突-bypass)
  - [7.2 1r1w — 无冲突（不同地址）](#72-1r1w--无冲突不同地址)
  - [7.3 1r1wm — Masked Bypass](#73-1r1wm--masked-bypass)
  - [7.4 连续冲突场景](#74-连续冲突场景)
- [8. 边界条件](#8-边界条件)
- [9. 配置约束](#9-配置约束)
- [10. 生成器架构](#10-生成器架构)

---

## 1. 适用范围与约束

| 类型 | 支持 L3 | 原因 |
|------|---------|------|
| 1r1w | **是** | 同步双端口，写读独立，同地址冲突需 bypass |
| 1r1wm | **是** | 同上，附加 mask 合并逻辑 |
| 1r1wa | 否 | 异步双时钟域，bypass 延迟无法对齐 |
| 1r1wma | 否 | 同上 |
| 1rw / 1rwm | 否 | 单端口，同周期只能读或写，不存在同时 R+W |
| 2rw / 2rwm | 否 | 真双端口各 port 独立，hazard 需上层处理 |
| rom | 否 | 只读，无写操作 |

---

## 2. 层次结构与命名

### 三层封装

```
L3: bypass_wrapper (_top)    ← 本文档
  └─ L2: memory_wrapper (_mem)    ← ECC + Init FSM + Pipeline
       └─ L1: physical_wrapper (_phy)    ← Tiling (col×row)
            └─ N×M vendor memory cells
```

### 模块命名后缀

| 配置 | L1 后缀 | L2 后缀 | L3 后缀 |
|------|---------|---------|---------|
| `enable_l2=false, enable_l3=false` | `_top` | — | — |
| `enable_l2=true,  enable_l3=false` | `_phy` | `_top` | — |
| `enable_l2=true,  enable_l3=true`  | `_phy` | `_mem` | `_top` |

完整模块名示例：
```
lx_CACHE_RAM_1r1w_32x256_top   (L3)
lx_CACHE_RAM_1r1w_32x256_mem   (L2)
lx_CACHE_RAM_1r1w_32x256_phy   (L1)
```

---

## 3. 接口规格

### 3.1 1r1w — Bypass Wrapper（无 Mask）

#### 端口列表

| 端口名 | 方向 | 位宽 | 说明 |
|--------|------|------|------|
| `i_clk` | input | 1 | 时钟 |
| `i_rst_n` | input | 1 | 低有效异步复位 |
| `i_init_en` | input | 1 | Init FSM 触发（直通 L2） |
| `i_init_value` | input | 1 | Init 填充值（直通 L2） |
| `o_init_done` | output | 1 | Init 完成标志（直通 L2） |
| `i_wr_en` | input | 1 | 写使能，**1 = 写** |
| `i_wr_addr` | input | `ADDR_WIDTH` | 写地址 |
| `i_wr_data` | input | `DATA_WIDTH` | 写数据 |
| `i_rd_en` | input | 1 | 读使能，**1 = 读** |
| `i_rd_addr` | input | `ADDR_WIDTH` | 读地址 |
| `o_rd_data` | output | `DATA_WIDTH` | 读数据（含 bypass 修正） |

#### ECC 可选端口（`ecc.enable=true` 时存在）

| 端口名 | 方向 | 位宽 | 说明 |
|--------|------|------|------|
| `i_ecc_en` | input | 1 | ECC 功能开关（直通 L2） |
| `i_ecc_err_insert` | input | 2 | 注错控制（直通 L2） |
| `i_ecc_err_mask` | input | 2 | 错误屏蔽（直通 L2） |
| `o_ecc_correctable_valid` | output | 1 | 可纠正错误有效位（**bypass 时 gate 为 0**） |
| `o_ecc_correctable_addr` | output | `ADDR_WIDTH` | 可纠正错误地址（直通 L2） |
| `o_ecc_uncorrectable_valid` | output | 1 | 不可纠正错误有效位（**bypass 时 gate 为 0**） |
| `o_ecc_uncorrectable_addr` | output | `ADDR_WIDTH` | 不可纠正错误地址（直通 L2） |
| `o_ecc_err_syndrome` | output | `ECC_M` | Syndrome（直通 L2，仅 `detailed_report=true`） |

### 3.2 1r1wm — Bypass Wrapper（带 Mask）

在 3.1 基础上增加：

| 端口名 | 方向 | 位宽 | 说明 |
|--------|------|------|------|
| `i_wr_bwen` | input | `MASK_WIDTH` | 按位写使能，**1 = 写入该 bit，0 = 保持**（MASK_WIDTH = DATA_WIDTH / mask_gran） |

> `o_rd_data` 的 bypass 行为：mask 写入的 bit 来自 `delayed_wdata`，未写入的 bit 来自 `l2_rd_data`。

---

## 4. 参数列表

| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| `DATA_WIDTH` | 配置值 | 用户数据位宽 |
| `RAM_DEPTH` | 配置值 | 存储深度 |
| `BYPASS_DEPTH` | 计算值 | Bypass pipeline 级数（见 §6.1） |
| `ADDR_WIDTH` | `$clog2(RAM_DEPTH)` | 地址位宽（派生） |

> `BYPASS_DEPTH` 由 `mem_gen.py` 在生成时计算并硬编码。

---

## 5. 微架构

### 5.1 总体结构

```
                     ┌───────────────────────────────────────────┐
                     │               L3 bypass_wrapper           │
                     │                                           │
  i_wr_en ──────────►├──────────►┌──────────────┐               │
  i_wr_addr ────────►├──────────►│              │               │
  i_wr_data ────────►├──────────►│   L2         │               │
  [i_wr_bwen] ─────►├──────────►│   memory     │               │
  i_rd_en ──────────►├──────────►│   _wrapper   │               │
  i_rd_addr ────────►├──────────►│              │               │
                     │           │              ├──► l2_rd_data  │
                     │           └──────────────┘       │        │
                     │                                  │        │
                     │  ┌─────────────────────┐         │        │
  i_wr_en ──────────►├─►│                     │         │        │
  i_rd_en ──────────►├─►│  bypass_hit (comb)  │         │        │
  i_wr_addr ────────►├─►│                     │         │        │
  i_rd_addr ────────►├─►│                     │         │        │
                     │  └────────┬────────────┘         │        │
                     │           │                      │        │
                     │  ┌────────▼────────────┐         │        │
  i_wr_data ────────►├─►│                     │         │        │
  [i_wr_bwen] ─────►├─►│  data_syncn         │         │        │
                     │  │  (BYPASS_DEPTH)     │         │        │
                     │  │                     │         │        │
                     │  └────────┬────────────┘         │        │
                     │           │                      │        │
                     │      delayed_hit                 │        │
                     │      delayed_wdata               │        │
                     │     [delayed_bwen]               │        │
                     │           │                      │        │
                     │  ┌────────▼──────────────────────▼───┐    │
                     │  │          Output Mux               │    │
                     │  └────────────────┬─────────────────┘    │
                     │                   │                       │
                     │               o_rd_data                  │
                     └───────────────────────────────────────────┘
```

### 5.2 冲突检测

组合逻辑，零延迟：

```verilog
wire bypass_hit = i_wr_en & i_rd_en & (i_wr_addr == i_rd_addr);
```

条件：写使能 **且** 读使能 **且** 地址相同。

### 5.3 Bypass Entry 打包

将 `bypass_hit` 标记和写数据打包为单一向量，送入延迟 pipeline：

**1r1w（无 mask）:**
```
bypass_entry_in = {bypass_hit, i_wr_data}
                   [DATA_WIDTH]  [DATA_WIDTH-1:0]
总宽度 = 1 + DATA_WIDTH
```

**1r1wm（带 mask）:**
```
bypass_entry_in = {bypass_hit, i_wr_bwen,              i_wr_data}
                   [2*DW]      [DW+:DW]                [DW-1:0]
总宽度 = 1 + DATA_WIDTH + DATA_WIDTH
```

> 打包顺序：MSB=hit, 中间=bwen（如有）, LSB=wdata。

### 5.4 延迟对齐 Pipeline

使用 `data_syncn` 模块（与 L2 一致的寄存器链）：

```verilog
data_syncn #(
    .RESET_VALUE ({(BYPASS_DATA_WIDTH){1'b0}}),
    .NUM_FLOPS   (BYPASS_DEPTH),
    .DATA_WIDTH  (BYPASS_DATA_WIDTH)
) u_bypass_pipe (
    .clk           (i_clk),
    .reset_n       (i_rst_n),
    .data_in       (bypass_entry_in),
    .data_out_sync (bypass_entry_out)
);
```

复位值为全 0 → 复位后 `delayed_hit=0`，输出 mux 选择 L2 正常读数据。

**解包：**

| 信号 | 1r1w | 1r1wm |
|------|------|-------|
| `delayed_hit` | `[DATA_WIDTH]` | `[2*DATA_WIDTH]` |
| `delayed_bwen` | — | `[DATA_WIDTH +: DATA_WIDTH]` |
| `delayed_wdata` | `[DATA_WIDTH-1:0]` | `[DATA_WIDTH-1:0]` |

### 5.5 输出 Mux

**1r1w（无 mask）— 全字替换：**

```verilog
assign o_rd_data = delayed_hit ? delayed_wdata : l2_rd_data;
```

**1r1wm（带 mask）— 按位合并：**

```verilog
assign o_rd_data = delayed_hit
    ? (delayed_wdata & delayed_bwen) | (l2_rd_data & ~delayed_bwen)
    : l2_rd_data;
```

语义：
- `delayed_bwen[i] = 1` → 该 bit 被写入 → 取 `delayed_wdata[i]`
- `delayed_bwen[i] = 0` → 该 bit 未被写入 → SRAM 旧值正确 → 取 `l2_rd_data[i]`

### 5.6 ECC 信号处理

bypass 有效时，L2 的 ECC decode 结果针对的是**被丢弃的 SRAM 旧数据**，不应向上层报告：

```verilog
// Gate off error valid when bypass is active
assign o_ecc_correctable_valid   = l2_ecc_correctable_valid   & ~delayed_hit;
assign o_ecc_uncorrectable_valid = l2_ecc_uncorrectable_valid & ~delayed_hit;

// Address and syndrome pass through unchanged (don't-care when valid=0)
assign o_ecc_correctable_addr    = l2_ecc_correctable_addr;
assign o_ecc_uncorrectable_addr  = l2_ecc_uncorrectable_addr;
assign o_ecc_err_syndrome        = l2_ecc_err_syndrome;  // if detailed_report
```

ECC 控制输入（`i_ecc_en`, `i_ecc_err_insert`, `i_ecc_err_mask`）直通 L2，L3 不修改。

### 5.7 直通信号

以下信号 L3 **不处理**，直接连线至 L2：

| 信号 | 连接方式 | 说明 |
|------|---------|------|
| `i_clk`, `i_rst_n` | L3 → L2 | 时钟复位 |
| `i_init_en`, `i_init_value` | L3 → L2 | Init FSM 控制 |
| `o_init_done` | L2 → L3 顶层 | Init 完成 |
| `i_wr_en/addr/data/bwen` | L3 → L2 | 写通道 |
| `i_rd_en`, `i_rd_addr` | L3 → L2 | 读控制 |
| `i_ecc_en/err_insert/err_mask` | L3 → L2 | ECC 控制 |

---

## 6. 时序分析

### 6.1 延迟模型

```
BYPASS_DEPTH = INPUT_PIPE_STAGES + RAM_RD_LATENCY + ECC_PIPE_STAGES + OUTPUT_PIPE_STAGES
```

| 配置示例 | INPUT | RAM_RD | ECC | OUTPUT | BYPASS_DEPTH |
|----------|-------|--------|-----|--------|-------------|
| 最小延迟 | 0 | 1 | 0 | 0 | 1 |
| 典型配置 | 1 | 1 | 0 | 1 | 3 |
| 带 ECC | 2 | 1 | 1 | 1 | 5 |
| 零延迟 | 0 | 0 | 0 | 0 | 0 |

> `BYPASS_DEPTH=0` 时 `data_syncn` 为组合直通（无寄存器），bypass_hit 立即生效。

### 6.2 为什么单条目 Bypass 足够

关键观察：**写入路径和读取路径共享 L2 的同一 input pipeline。**

```
Cycle N:  i_wr_en=1, i_rd_en=1, i_wr_addr==i_rd_addr  ← bypass_hit=1

          ┌─ Write data enters L2 input pipe ──►  SRAM write at cycle N+INPUT_PIPE
          └─ Read  addr enters L2 input pipe ──►  SRAM read  at cycle N+INPUT_PIPE

因为写和读同时进入 L2 input pipe，写数据到达 SRAM 和读数据从 SRAM 出来的相对时序是固定的。
```

后续每个 pipeline stage（ECC decode、output pipe）对写数据和读数据施加相同延迟。因此：

- **不需要 multi-entry bypass queue** — 同一周期只可能有一个冲突地址
- **不需要 bypass 有效位检查链** — 延迟精确匹配，只需简单的 N 级 FF

唯一的假设：L2 的 input pipeline 是**统一的**（写和读信号在同一个 `data_syncn` 中传输，或使用相同级数的独立 pipeline）。当前 L2 实现（sync DualPort）满足此假设。

---

## 7. 时序图

### 7.1 1r1w — 冲突 Bypass

> 配置: INPUT_PIPE=0, RAM_RD_LATENCY=1, ECC_PIPE=0, OUTPUT_PIPE=0 → BYPASS_DEPTH=1

```wavedrom
{
  "signal": [
    {"name": "clk",        "wave": "p........."},
    {},
    ["Write",
      {"name": "i_wr_en",   "wave": "01..0....."},
      {"name": "i_wr_addr", "wave": "x=..x.....", "data": ["A0"]},
      {"name": "i_wr_data", "wave": "x=..x.....", "data": ["D_new"]}
    ],
    {},
    ["Read",
      {"name": "i_rd_en",   "wave": "01..0....."},
      {"name": "i_rd_addr", "wave": "x=..x.....", "data": ["A0"]}
    ],
    {},
    ["Internal",
      {"name": "bypass_hit",    "wave": "01..0....."},
      {"name": "delayed_hit",   "wave": "0.1..0...."},
      {"name": "delayed_wdata", "wave": "x.=..x....", "data": ["D_new"]},
      {"name": "l2_rd_data",    "wave": "x.=..x....", "data": ["D_old"]}
    ],
    {},
    {"name": "o_rd_data",  "wave": "x.=..x....", "data": ["D_new ✓"]}
  ],
  "head": {"text": "1r1w Bypass: Same Address R+W → Output = Write Data"},
  "config": {"hscale": 1.3}
}
```

### 7.2 1r1w — 无冲突（不同地址）

```wavedrom
{
  "signal": [
    {"name": "clk",        "wave": "p........."},
    {},
    ["Write",
      {"name": "i_wr_en",   "wave": "01..0....."},
      {"name": "i_wr_addr", "wave": "x=..x.....", "data": ["A0"]},
      {"name": "i_wr_data", "wave": "x=..x.....", "data": ["D0"]}
    ],
    {},
    ["Read",
      {"name": "i_rd_en",   "wave": "01..0....."},
      {"name": "i_rd_addr", "wave": "x=..x.....", "data": ["A1"]}
    ],
    {},
    ["Internal",
      {"name": "bypass_hit",    "wave": "0........."},
      {"name": "delayed_hit",   "wave": "0........."},
      {"name": "l2_rd_data",    "wave": "x.=..x....", "data": ["D1"]}
    ],
    {},
    {"name": "o_rd_data",  "wave": "x.=..x....", "data": ["D1 (from SRAM)"]}
  ],
  "head": {"text": "1r1w No Conflict: Different Addresses → Normal SRAM Read"},
  "config": {"hscale": 1.3}
}
```

### 7.3 1r1wm — Masked Bypass

> 场景: 写 A0 with mask=0x0F (低 4 bit 写入), 同时读 A0

```wavedrom
{
  "signal": [
    {"name": "clk",        "wave": "p........."},
    {},
    ["Write",
      {"name": "i_wr_en",   "wave": "01..0....."},
      {"name": "i_wr_addr", "wave": "x=..x.....", "data": ["A0"]},
      {"name": "i_wr_data", "wave": "x=..x.....", "data": ["0xABCD"]},
      {"name": "i_wr_bwen", "wave": "x=..x.....", "data": ["0x00FF"]}
    ],
    {},
    ["Read",
      {"name": "i_rd_en",   "wave": "01..0....."},
      {"name": "i_rd_addr", "wave": "x=..x.....", "data": ["A0"]}
    ],
    {},
    ["Internal",
      {"name": "bypass_hit",     "wave": "01..0....."},
      {"name": "delayed_hit",    "wave": "0.1..0...."},
      {"name": "delayed_wdata",  "wave": "x.=..x....", "data": ["0xABCD"]},
      {"name": "delayed_bwen",   "wave": "x.=..x....", "data": ["0x00FF"]},
      {"name": "l2_rd_data",     "wave": "x.=..x....", "data": ["0x1234"]}
    ],
    {},
    {"name": "o_rd_data",  "wave": "x.=..x....", "data": ["0x12CD ✓"]}
  ],
  "head": {"text": "1r1wm Masked Bypass: hi=SRAM(0x12), lo=Write(0xCD)"},
  "config": {"hscale": 1.3},
  "foot": {"text": "o_rd_data = (0xABCD & 0x00FF) | (0x1234 & 0xFF00) = 0x12CD"}
}
```

### 7.4 连续冲突场景

> 连续两个周期写不同地址，同时读对应地址

```wavedrom
{
  "signal": [
    {"name": "clk",        "wave": "p.........."},
    {},
    ["Write",
      {"name": "i_wr_en",   "wave": "011..0....."},
      {"name": "i_wr_addr", "wave": "x==..x.....", "data": ["A0", "A1"]},
      {"name": "i_wr_data", "wave": "x==..x.....", "data": ["D0", "D1"]}
    ],
    {},
    ["Read",
      {"name": "i_rd_en",   "wave": "011..0....."},
      {"name": "i_rd_addr", "wave": "x==..x.....", "data": ["A0", "A1"]}
    ],
    {},
    ["Internal",
      {"name": "bypass_hit",    "wave": "011..0....."},
      {"name": "delayed_hit",   "wave": "0.11..0...."},
      {"name": "l2_rd_data",    "wave": "x.==..x....", "data": ["stale0", "stale1"]}
    ],
    {},
    {"name": "o_rd_data",  "wave": "x.==..x....", "data": ["D0 ✓", "D1 ✓"]}
  ],
  "head": {"text": "Consecutive Conflicts: Each Cycle Independently Bypassed"},
  "config": {"hscale": 1.3}
}
```

---

## 8. 边界条件

| 场景 | 行为 | 正确性 |
|------|------|--------|
| `BYPASS_DEPTH = 0` | `data_syncn` 组合直通，`delayed_hit = bypass_hit`（同周期生效） | 正确 |
| 仅写无读 (`i_rd_en=0`) | `bypass_hit=0` → `delayed_hit=0` → 无 bypass | 正确 |
| 仅读无写 (`i_wr_en=0`) | `bypass_hit=0` → 正常 SRAM 读 | 正确 |
| 写读不同地址 | `bypass_hit=0` → 正常 SRAM 读 | 正确 |
| `i_rst_n=0` 复位 | `data_syncn` 复位为全 0 → `delayed_hit=0` → 无 bypass | 正确 |
| Init FSM 运行中 | Init 信号直通 L2，bypass 检测仍基于 `i_wr_en/i_rd_en`（用户不应在 init 期间执行读写） | 正确（前提） |
| ECC bypass 冲突 | bypass 有效 → `ecc_*_valid` gate 为 0 → 不报告旧数据的 ECC 错误 | 正确 |
| 1r1wm mask 全 0 (`bwen=0`) | 无 bit 被写入 → 但 `bypass_hit=1` → mux 输出 `(wdata & 0) | (l2_rd_data & ~0) = l2_rd_data` | 正确 |
| 1r1wm mask 全 1 (`bwen=~0`) | 全 bit 被写入 → mux 输出 `(wdata & ~0) | (l2_rd_data & 0) = wdata` | 正确 |

---

## 9. 配置约束

在 `mem_config.json` 中设置 `"enable_l3": true` 时，以下约束由 `config_io.py` 校验：

| 约束 | 校验 | 错误信息 |
|------|------|---------|
| `enable_l2` 必须为 `true` | `_validate_memory()` | `enable_l3=true requires enable_l2=true` |
| `interface_type.base_type` 必须为 `dual_port` | `_validate_memory()` | `only supported for sync dual_port types` |
| `interface_type.is_async` 必须为 `false` | `_validate_memory()` | 同上 |

配置示例：

```jsonc
{
  "name": "cache_data",
  "type": "1r1w",
  "width": 128,
  "depth": 256,
  "enable_l2": true,
  "enable_l3": true,          // ← 启用 bypass wrapper
  "ram_rd_latency": 1,
  "input_pipe_stages": 1,
  "ecc_pipe_stages": 0,
  "output_pipe_stages": 1,
  "ecc": { "enable": false },
  "physical": {
    "sub_type": "uhd2prf",
    "lib_name": "SRAM_DP_256x128",
    "lib_width": 128,
    "lib_depth": 256
  }
}
```

---

## 10. 生成器架构

```
bypass_wrapper_gen.py
├── BypassWrapperGenerator (ABC)
│   ├── build_context()     → dict    [abstract]
│   └── generate()          → str     [template render]
│
└── DualPortBypassGen (concrete)
    └── build_context()     → dict    [1r1w / 1r1wm]

GENERATORS = {"dual_port": DualPortBypassGen()}

gen_bypass_wrapper(mem_spec, ecc_params, interface_type, module_name, l2_name) → str
```

**Context dict 关键字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `module_name` | str | L3 模块名 |
| `l2_wrapper_name` | str | L2 模块名 |
| `data_width` | int | 用户数据位宽 |
| `ram_depth` | int | 存储深度 |
| `bypass_depth` | int | Bypass pipeline 级数 |
| `bypass_data_width` | str | Bypass entry 总宽度表达式 |
| `has_mask` | bool | 是否有 mask |
| `has_ecc` | bool | 是否有 ECC |
| `detailed_report` | bool | 是否输出 syndrome |
| `module_ports` | list[str] | 顶层端口声明 |
| `l2_inst_ports` | list[str] | L2 实例端口连接 |
| `ecc_m` | int | ECC parity 位宽（仅 has_ecc=true） |
