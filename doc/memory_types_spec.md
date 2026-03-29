# SRAM Memory Types — Physical Wrapper 接口规格与时序说明

> 本文档描述 `physical_wrapper` 层（Layer 1）封装的所有 memory 类型的接口定义、位宽规则和读写时序。
> 所有接口均为 **active-HIGH** 语义（与 vendor cell 的极性无关，极性转换由 `vendor_port_map.json` 的 `port_map` 处理）。

---

## 目录

- [1. 类型总览](#1-类型总览)
- [2. Single-Port 类型](#2-single-port-类型)
  - [2.1 1rw — 单端口读写（无 Mask）](#21-1rw--单端口读写无-mask)
  - [2.2 1rwm — 单端口读写（带 Mask）](#22-1rwm--单端口读写带-mask)
- [3. Dual-Port 类型](#3-dual-port-类型)
  - [3.1 1r1w — 双端口读写（无 Mask，同步）](#31-1r1w--双端口读写无-mask同步)
  - [3.2 1r1wm — 双端口读写（带 Mask，同步）](#32-1r1wm--双端口读写带-mask同步)
  - [3.3 1r1wa — 双端口读写（无 Mask，异步）](#33-1r1wa--双端口读写无-mask异步)
  - [3.4 1r1wma — 双端口读写（带 Mask，异步）](#34-1r1wma--双端口读写带-mask异步)
- [4. True Dual-Port 类型](#4-true-dual-port-类型)
  - [4.1 2rw — 真双端口（无 Mask）](#41-2rw--真双端口无-mask)
  - [4.2 2rwm — 真双端口（带 Mask）](#42-2rwm--真双端口带-mask)
- [5. ROM 类型](#5-rom-类型)
  - [5.1 rom — 只读存储器](#51-rom--只读存储器)
- [6. 位宽配置规则汇总](#6-位宽配置规则汇总)
- [7. 添加新类型操作指导](#7-添加新类型操作指导)

---

## 1. 类型总览

| 类型 | base_type | has_mask | async | 端口数 | 状态 |
|------|-----------|----------|-------|--------|------|
| `1rw` | single_port | false | — | 1 | 已实现 |
| `1rwm` | single_port | true | — | 1 | 已实现 |
| `1r1w` | dual_port | false | false | 2 | 已实现 |
| `1r1wm` | dual_port | true | false | 2 | 已实现 |
| `1r1wa` | dual_port | false | true | 2 | 预留 |
| `1r1wma` | dual_port | true | true | 2 | 预留 |
| `2rw` | true_dual_port | false | — | 2 | 预留 |
| `2rwm` | true_dual_port | true | — | 2 | 预留 |
| `rom` | rom | false | — | 1 | 已实现 |

---

## 2. Single-Port 类型

单端口 memory，读和写共享同一组地址/数据端口。同一周期内要么读、要么写，不可同时进行。

### 2.1 1rw — 单端口读写（无 Mask）

#### 接口列表

| 端口名 | 方向 | 位宽 | 说明 |
|--------|------|------|------|
| `clk` | input | 1 | 时钟 |
| `cen` | input | 1 | 片选使能，**1 = 使能** |
| `wen` | input | 1 | 写使能，**1 = 写，0 = 读** |
| `addr` | input | `clog2(DEPTH)` | 地址 |
| `wdata` | input | `WIDTH` | 写数据 |
| `rdata` | output | `WIDTH` | 读数据（1 周期延迟） |

> Mask 端口（BWEB）由 sub_type 的 `const_ports` 静态 tie-off 为 0（全写入），不出现在 wrapper 接口。

#### 写操作时序

```wavedrom
{
  "signal": [
    {"name": "clk",   "wave": "p......"},
    {"name": "cen",   "wave": "01..0.."},
    {"name": "wen",   "wave": "01..0.."},
    {"name": "addr",  "wave": "x=..x..", "data": ["A0"]},
    {"name": "wdata", "wave": "x=..x..", "data": ["D0"]},
    {"name": "rdata", "wave": "x......"}
  ],
  "head": {"text": "1rw Write Operation"},
  "config": {"hscale": 1.2}
}
```

#### 读操作时序

```wavedrom
{
  "signal": [
    {"name": "clk",   "wave": "p......"},
    {"name": "cen",   "wave": "01..0.."},
    {"name": "wen",   "wave": "0.....0"},
    {"name": "addr",  "wave": "x=..x..", "data": ["A1"]},
    {"name": "wdata", "wave": "x......"},
    {"name": "rdata", "wave": "x..=x..", "data": ["D1"]}
  ],
  "head": {"text": "1rw Read Operation (latency = 1)"},
  "config": {"hscale": 1.2}
}
```

#### 连续读写时序

```wavedrom
{
  "signal": [
    {"name": "clk",   "wave": "p........"},
    {"name": "cen",   "wave": "01111.0.."},
    {"name": "wen",   "wave": "01100.0.."},
    {"name": "addr",  "wave": "x====.x..", "data": ["A0", "A1", "A0", "A1"]},
    {"name": "wdata", "wave": "x==xx.x..", "data": ["D0", "D1"]},
    {"name": "rdata", "wave": "x....==x.", "data": ["D0", "D1"]}
  ],
  "head": {"text": "1rw Write A0,A1 then Read A0,A1"},
  "config": {"hscale": 1.2}
}
```

---

### 2.2 1rwm — 单端口读写（带 Mask）

#### 接口列表

| 端口名 | 方向 | 位宽 | 说明 |
|--------|------|------|------|
| `clk` | input | 1 | 时钟 |
| `cen` | input | 1 | 片选使能，**1 = 使能** |
| `wen` | input | 1 | 写使能，**1 = 写，0 = 读** |
| `addr` | input | `clog2(DEPTH)` | 地址 |
| `wdata` | input | `WIDTH` | 写数据 |
| `rdata` | output | `WIDTH` | 读数据（1 周期延迟） |
| `bwen` | input | `MASK_WIDTH` | 分组写使能，**1 = 写入对应 `mask_gran` 个 bit，0 = 保持**（`mask_gran=1` 时等同按位） |

#### Masked 写操作时序

```wavedrom
{
  "signal": [
    {"name": "clk",   "wave": "p......."},
    {"name": "cen",   "wave": "01...0.."},
    {"name": "wen",   "wave": "01...0.."},
    {"name": "addr",  "wave": "x=...x..", "data": ["A0"]},
    {"name": "wdata", "wave": "x=...x..", "data": ["D_new"]},
    {"name": "bwen",  "wave": "x=...x..", "data": ["MASK"]},
    {"name": "rdata", "wave": "x......."},
    {},
    {"name": "说明", "wave": "x=...x..", "data": ["group[g]: bwen[g]=1 → write D_new[g*GRAN +: GRAN], bwen[g]=0 → keep old"]}
  ],
  "head": {"text": "1rwm Masked Write (MASK_WIDTH = WIDTH / mask_gran)"},
  "config": {"hscale": 1.2}
}
```

#### 读操作时序

与 1rw 完全一致（`bwen` 在读操作时被忽略）。

---

## 3. Dual-Port 类型

双端口 memory，读和写拥有**独立**的地址和使能端口。可在同一周期同时读和写。

### 3.1 1r1w — 双端口读写（无 Mask，同步）

#### 接口列表

| 端口名 | 方向 | 位宽 | 说明 |
|--------|------|------|------|
| `clk` | input | 1 | 共享时钟 |
| `wr_en` | input | 1 | 写使能，**1 = 写** |
| `wr_addr` | input | `clog2(DEPTH)` | 写地址 |
| `wr_data` | input | `WIDTH` | 写数据 |
| `rd_en` | input | 1 | 读使能，**1 = 读** |
| `rd_addr` | input | `clog2(DEPTH)` | 读地址 |
| `rd_data` | output | `WIDTH` | 读数据（1 周期延迟） |

> Mask 端口（BWEB）由 sub_type 的 `const_ports` 静态 tie-off 为 0（全写入），不出现在 wrapper 接口。

#### 同时读写时序

```wavedrom
{
  "signal": [
    {"name": "clk",     "wave": "p........"},
    {},
    ["Write Port",
      {"name": "wr_en",   "wave": "01...0..."},
      {"name": "wr_addr", "wave": "x===.x...", "data": ["A0", "A1", "A2"]},
      {"name": "wr_data", "wave": "x===.x...", "data": ["D0", "D1", "D2"]}
    ],
    {},
    ["Read Port",
      {"name": "rd_en",   "wave": "0..1..0.."},
      {"name": "rd_addr", "wave": "x..==.x..", "data": ["A0", "A1"]},
      {"name": "rd_data", "wave": "x....==x.", "data": ["D0", "D1"]}
    ]
  ],
  "head": {"text": "1r1w Concurrent Read & Write"},
  "config": {"hscale": 1.2}
}
```

> **注意**: 同一周期对同一地址同时读写的行为**未定义**（输出可能为 X）。

---

### 3.2 1r1wm — 双端口读写（带 Mask，同步）

#### 接口列表

| 端口名 | 方向 | 位宽 | 说明 |
|--------|------|------|------|
| `clk` | input | 1 | 共享时钟 |
| `wr_en` | input | 1 | 写使能 |
| `wr_addr` | input | `clog2(DEPTH)` | 写地址 |
| `wr_data` | input | `WIDTH` | 写数据 |
| `wr_mask` | input | `MASK_WIDTH` | 分组写使能，**1 = 写入，0 = 保持**（`mask_gran=1` 时等同按位） |
| `rd_en` | input | 1 | 读使能 |
| `rd_addr` | input | `clog2(DEPTH)` | 读地址 |
| `rd_data` | output | `WIDTH` | 读数据（1 周期延迟） |

#### Masked 写 + 读时序

```wavedrom
{
  "signal": [
    {"name": "clk",     "wave": "p......."},
    {},
    ["Write Port",
      {"name": "wr_en",   "wave": "01..0..."},
      {"name": "wr_addr", "wave": "x=..x...", "data": ["A0"]},
      {"name": "wr_data", "wave": "x=..x...", "data": ["D_new"]},
      {"name": "wr_mask", "wave": "x=..x...", "data": ["MASK"]}
    ],
    {},
    ["Read Port",
      {"name": "rd_en",   "wave": "0.1..0.."},
      {"name": "rd_addr", "wave": "x.=..x..", "data": ["A0"]},
      {"name": "rd_data", "wave": "x...=.x.", "data": ["D_merged"]}
    ],
    {},
    {"name": "说明", "wave": "x...=.x.", "data": ["D_merged = (D_old & ~MASK) | (D_new & MASK)"]}
  ],
  "head": {"text": "1r1wm Masked Write then Read"},
  "config": {"hscale": 1.2}
}
```

---

### 3.3 1r1wa — 双端口读写（无 Mask，异步）

与 `1r1w` 功能一致，但读写时钟**独立**。

#### 接口列表

| 端口名 | 方向 | 位宽 | 说明 |
|--------|------|------|------|
| `wr_clk` | input | 1 | **写时钟** |
| `rd_clk` | input | 1 | **读时钟** |
| `wr_en` | input | 1 | 写使能（`wr_clk` 域） |
| `wr_addr` | input | `clog2(DEPTH)` | 写地址 |
| `wr_data` | input | `WIDTH` | 写数据 |
| `rd_en` | input | 1 | 读使能（`rd_clk` 域） |
| `rd_addr` | input | `clog2(DEPTH)` | 读地址 |
| `rd_data` | output | `WIDTH` | 读数据（`rd_clk` 域，1 周期延迟） |

> Mask 端口（BWEB）由 sub_type 的 `const_ports` 静态 tie-off 为 0。

#### 异步读写时序

```wavedrom
{
  "signal": [
    {"name": "wr_clk",  "wave": "p......."},
    {"name": "rd_clk",  "wave": "P......."},
    {},
    ["Write (wr_clk domain)",
      {"name": "wr_en",   "wave": "01..0..."},
      {"name": "wr_addr", "wave": "x=..x...", "data": ["A0"]},
      {"name": "wr_data", "wave": "x=..x...", "data": ["D0"]}
    ],
    {},
    ["Read (rd_clk domain)",
      {"name": "rd_en",   "wave": "0...1.0."},
      {"name": "rd_addr", "wave": "x...=.x.", "data": ["A0"]},
      {"name": "rd_data", "wave": "x....=x.", "data": ["D0"]}
    ]
  ],
  "head": {"text": "1r1wa Async Dual-Clock Read & Write"},
  "config": {"hscale": 1.2},
  "foot": {"text": "注意：读必须在写完成之后（跨时钟域同步由上层负责）"}
}
```

---

### 3.4 1r1wma — 双端口读写（带 Mask，异步）

与 `1r1wa` 相同的双时钟结构，增加 `wr_mask` 端口。

#### 接口列表

| 端口名 | 方向 | 位宽 | 说明 |
|--------|------|------|------|
| `wr_clk` | input | 1 | 写时钟 |
| `rd_clk` | input | 1 | 读时钟 |
| `wr_en` | input | 1 | 写使能（`wr_clk` 域） |
| `wr_addr` | input | `clog2(DEPTH)` | 写地址 |
| `wr_data` | input | `WIDTH` | 写数据 |
| `wr_mask` | input | `MASK_WIDTH` | 分组写使能，**1 = 写入，0 = 保持**（`mask_gran=1` 时等同按位） |
| `rd_en` | input | 1 | 读使能（`rd_clk` 域） |
| `rd_addr` | input | `clog2(DEPTH)` | 读地址 |
| `rd_data` | output | `WIDTH` | 读数据（`rd_clk` 域，1 周期延迟） |

时序与 `1r1wa` 一致，写操作额外受 `wr_mask` 控制。

---

## 4. True Dual-Port 类型

真双端口 memory，两个端口（A/B）均可独立进行读或写操作，各自拥有独立的时钟、地址和数据通路。

### 4.1 2rw — 真双端口（无 Mask）

#### 接口列表

**Port A:**

| 端口名 | 方向 | 位宽 | 说明 |
|--------|------|------|------|
| `a_clk` | input | 1 | A 端口时钟 |
| `a_cen` | input | 1 | A 端口片选，**1 = 使能** |
| `a_wen` | input | 1 | A 端口写使能，**1 = 写，0 = 读** |
| `a_addr` | input | `clog2(DEPTH)` | A 端口地址 |
| `a_wdata` | input | `WIDTH` | A 端口写数据 |
| `a_rdata` | output | `WIDTH` | A 端口读数据（1 周期延迟） |

**Port B:**

| 端口名 | 方向 | 位宽 | 说明 |
|--------|------|------|------|
| `b_clk` | input | 1 | B 端口时钟 |
| `b_cen` | input | 1 | B 端口片选，**1 = 使能** |
| `b_wen` | input | 1 | B 端口写使能，**1 = 写，0 = 读** |
| `b_addr` | input | `clog2(DEPTH)` | B 端口地址 |
| `b_wdata` | input | `WIDTH` | B 端口写数据 |
| `b_rdata` | output | `WIDTH` | B 端口读数据（1 周期延迟） |

> Mask 端口（BWEBA/BWEBB）由 sub_type 的 `const_ports` 静态 tie-off 为 0。

#### A 写 / B 读并发时序

```wavedrom
{
  "signal": [
    {"name": "a_clk",   "wave": "p......."},
    {"name": "b_clk",   "wave": "p......."},
    {},
    ["Port A (Write)",
      {"name": "a_cen",   "wave": "01..0..."},
      {"name": "a_wen",   "wave": "01..0..."},
      {"name": "a_addr",  "wave": "x=..x...", "data": ["A0"]},
      {"name": "a_wdata", "wave": "x=..x...", "data": ["D0"]}
    ],
    {},
    ["Port B (Read)",
      {"name": "b_cen",   "wave": "0.1..0.."},
      {"name": "b_wen",   "wave": "0......0"},
      {"name": "b_addr",  "wave": "x.=..x..", "data": ["A0"]},
      {"name": "b_rdata", "wave": "x...=.x.", "data": ["D0"]}
    ]
  ],
  "head": {"text": "2rw Port A Write, Port B Read"},
  "config": {"hscale": 1.2},
  "foot": {"text": "注意：同周期同地址 A 写 B 读行为未定义"}
}
```

---

### 4.2 2rwm — 真双端口（带 Mask）

与 `2rw` 相同，每个端口增加独立的 `bwen`。

#### 接口列表（在 2rw 基础上新增）

| 端口名 | 方向 | 位宽 | 说明 |
|--------|------|------|------|
| `a_bwen` | input | `MASK_WIDTH` | A 端口分组写使能，**1 = 写入，0 = 保持**（`mask_gran=1` 时等同按位） |
| `b_bwen` | input | `MASK_WIDTH` | B 端口分组写使能 |

其余端口同 `2rw`。

---

## 5. ROM 类型

### 5.1 rom — 只读存储器

#### 接口列表

| 端口名 | 方向 | 位宽 | 说明 |
|--------|------|------|------|
| `clk` | input | 1 | 时钟 |
| `cen` | input | 1 | 片选使能，**1 = 使能** |
| `addr` | input | `clog2(DEPTH)` | 地址 |
| `rdata` | output | `WIDTH` | 读数据（1 周期延迟） |

#### 读操作时序

```wavedrom
{
  "signal": [
    {"name": "clk",   "wave": "p......."},
    {"name": "cen",   "wave": "01110..."},
    {"name": "addr",  "wave": "x===x...", "data": ["A0", "A1", "A2"]},
    {"name": "rdata", "wave": "x.===x..", "data": ["D0", "D1", "D2"]}
  ],
  "head": {"text": "ROM Consecutive Reads (latency = 1)"},
  "config": {"hscale": 1.2}
}
```

---

## 6. 位宽配置规则汇总

| 端口类别 | 位宽公式 | 说明 |
|----------|---------|------|
| 地址 (`addr` / `*_addr`) | `clog2(DEPTH)` | DEPTH 为物理 memory 深度 |
| 写数据 (`wdata` / `wr_data` / `*_wdata`) | `WIDTH` | WIDTH 为物理 memory 数据宽度 |
| 读数据 (`rdata` / `rd_data` / `*_rdata`) | `WIDTH` | 同上 |
| 写掩码 (`bwen` / `wr_mask` / `*_bwen`) | `MASK_WIDTH` | `MASK_WIDTH = WIDTH / mask_gran`，默认 `mask_gran=1` 时等于 `WIDTH` |
| 时钟 / 使能信号 | `1` | 单 bit |

> **Tiling 场景**：当逻辑 memory 的 WIDTH 或 DEPTH 超过物理 cell 的容量时，`physical_wrapper` 自动进行宽度/深度拼接：
> - **宽度拼接**：`col_count = ceil(WIDTH_logical / WIDTH_phy)`，数据按列切分
> - **深度拼接**：`row_count = ceil(DEPTH_logical / DEPTH_phy)`，地址高位做片选
> - 拼接对外部接口**透明**，接口位宽以逻辑 memory 的 WIDTH/DEPTH 为准
> - **Mask padding**：tiling 补位产生的 padding data bit 对应的 mask bit 内部固定为 1（始终写入），对外接口不暴露

### 6.1 Mask 粒度规则

#### 术语定义

| 术语 | 计算公式 | 含义 |
|------|---------|------|
| `lib_mask_width` | 配置项 `physical.lib_mask_width` | 每个 vendor cell 的 mask bit 数 |
| `mask_gran` | `lib_width / lib_mask_width` | 每个 mask bit 控制的 data bit 数 |
| `MASK_WIDTH` | `WIDTH / mask_gran` | 封装器对外暴露的 mask 端口宽度 |

#### 展开行为

写掩码在 **L1（physical_wrapper）内部**从 `MASK_WIDTH` 展开至 `lib_width`（bit-level），再连接 vendor cell 的 BWEB：

```
mask[g] → expanded[g×mask_gran +: mask_gran] = {mask_gran{mask[g]}}
```

- `mask[g]=1`：对应的 `mask_gran` 个 data bit **全部写入**
- `mask[g]=0`：对应的 `mask_gran` 个 data bit **全部保持**
- 展开为纯组合逻辑，不增加时序路径

#### 配置约束（config_io.py 校验）

| 约束 | 条件 | 说明 |
|------|------|------|
| mask 粒度整数 | `lib_width % lib_mask_width == 0` | 每个 cell 的数据宽度必须被 mask 位数整除 |
| 逻辑宽度对齐 | `WIDTH % mask_gran == 0` | 用户逻辑宽度必须是 mask_gran 的整数倍 |
| ECC slice 对齐 | `ECC_SLICE_NUM % lib_mask_width == 0`（ECC 使能时） | 每个 mask bit 必须覆盖完整的 ECC slice（data + parity 不可拆分） |

#### 典型配置示例

| 场景 | `lib_width` | `lib_mask_width` | `mask_gran` | `WIDTH` | `MASK_WIDTH` |
|------|------------|-----------------|-------------|---------|-------------|
| Bit-level（默认） | 32 | 32 | 1 | 32 | 32 |
| Byte-mask | 32 | 4 | 8 | 32 | 4 |
| Word-mask | 32 | 1 | 32 | 32 | 1 |
| ECC + byte-mask | 56 | 4 | 14 | 48 | — *(WIDTH/mask_gran=48/14 非整数，不合法)* |
| ECC + slice-mask | 56 | 4 | 14 | 56 | 4 *(slice_count=4, 4%4=0 ✓)* |

---

## 7. 添加新类型操作指导

### 7.1 判断是否需要新类型

新增一个 vendor memory cell 时，首先判断它是否属于已有的 `interface_type`：

```
已有 port_map 能覆盖所有功能端口？
  ├─ YES → 仅需在对应 interface_type 的 sub_types 中添加一条记录
  └─ NO  → 需要新增 interface_type
```

### 7.2 添加新 Sub-Type（最常见场景）

**场景**：新 vendor cell 功能端口与已有类型完全一致，仅 const 端口不同。

**步骤**：

1. **确认 interface_type**：对照 vendor 提供的 cell 文档，找到匹配的 `interface_type`

2. **编辑 `vendor_port_map.json`**：在对应 `interface_type` 的 `sub_types` 数组中添加新条目

```jsonc
// 例：在 1rwm 下添加新的 sub_type "new_cell_family"
{
  "names": ["new_cell_family"],
  "const_ports": {
    // 从 vendor cell 文档中提取所有 const 端口（值为 0/1/Verilog 常量的）
    "BIST": 0, "SCAN_EN": 0, ...
    "RD_MARGIN": "{2'b10}", "WR_MARGIN": "{2'b01}",
    "NEW_PIN": "{1'b1}"     // 新 cell 特有的 const 端口
  },
  "output_ports": ["SCAN_OUT_C", "SCAN_OUT_D", ...]
  // 从 vendor cell 文档中提取所有不使用的输出端口
}
```

3. **如果 const ports 与已有 sub_type 完全一致**：将新名称加入已有条目的 `names` 数组即可

```jsonc
// 例：new_variant 与 1prf 完全一致
{ "names": ["1prf", "uhd1prf", ..., "new_variant"], "const_ports": { ... } }
```

4. **同步维护**：如果该 cell 同时适用于 mask 和非 mask 类型（如 1rw 和 1rwm），需在**两个** interface_type 中都添加（非 mask 类型的 const_ports 额外包含 `"BWEB": 0`）

### 7.3 添加新 Interface Type

**场景**：新 cell 的功能端口布局与所有现有类型都不匹配。

**步骤**：

1. **定义命名**：遵循 `<端口数><功能><修饰符>` 格式
   - 端口数：`1` = 单端口，`2` = 真双端口，`1r1w` = 读写分离
   - 功能：`rw` = 读写，`r` = 只读
   - 修饰符：`m` = mask，`a` = async

2. **定义 port_map**：为每个功能端口分配 logical name → vendor pin 映射

```jsonc
"new_type": {
  "base_type": "new_base_type",   // 或复用已有 base_type
  "has_mask": true,
  "async": false,                  // dual_port 必须声明
  "port_map": {
    "clk":     "CLK",
    "new_en":  "~NEW_EN_PIN",     // ~ 表示 vendor 端口极性取反
    "addr":    "A",
    "wdata":   "D",
    "rdata":   "Q"
  },
  "sub_types": [ ... ]
}
```

3. **实现 Generator**：在 `scripts/physical_wrapper_gen.py` 中

   - 如果 `base_type` 已存在（如 single_port），已有 Generator 可直接复用
   - 如果是全新 `base_type`，需新增 Generator 子类并注册到 `GENERATORS` 字典

4. **添加 sub_types**：按 7.2 步骤添加

5. **更新测试矩阵**：在 `mem_config.json` 中添加至少 2 个实例覆盖新类型

### 7.4 端口分类速查

从 vendor cell 文档提取端口时，按值类型分类：

| 值格式 | 分类 | 放入位置 |
|--------|------|---------|
| `"addr"`, `"~cen"`, `"clk"` 等字符串 | 功能端口 | `port_map`（`~` 表示取反） |
| `0`, `1` | 常量输入 | sub_type 的 `const_ports` |
| `"{2'b10}"`, `"2'b11"` | Verilog 常量输入 | sub_type 的 `const_ports` |
| `-1` | 输出端口（悬空） | sub_type 的 `output_ports` |

### 7.5 Checklist

添加新类型后，确认以下事项：

- [ ] `vendor_port_map.json` 中 port_map 覆盖所有功能端口
- [ ] sub_type 的 `const_ports` + `output_ports` 覆盖所有非功能端口
- [ ] `has_mask: false` 的类型 port_map 中**不含** mask 端口，BWEB 在 const_ports 中
- [ ] `has_mask: true` 的类型 port_map 中**包含** mask 端口
- [ ] dual_port 类型声明了 `async` 属性
- [ ] 非 mask / mask 变体各自独立维护 sub_types
- [ ] mask 类型配置了 `physical.lib_mask_width`，且 `lib_width % lib_mask_width == 0`
- [ ] `WIDTH % mask_gran == 0`（逻辑数据宽度是 mask 粒度的整数倍）
- [ ] ECC 使能时：`ECC_SLICE_NUM % lib_mask_width == 0`（mask bit 对齐 ECC slice 边界）
- [ ] `mem_config.json` 中有对应的测试实例
- [ ] Vendor 行为模型已生成并通过仿真验证
