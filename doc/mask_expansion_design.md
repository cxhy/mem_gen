# Mask 粒度展开设计 — 任务规划

> 基于 2026-03-25 架构审查，定义 L1 mask 展开功能的设计规格与实施任务。

## 1. 背景与动机

### 1.1 现状

当前 mask 端口在所有层级都是 bit-level（`MASK_WIDTH == DATA_WIDTH`，每个 mask bit 控制 1 个 data bit）。`memory_types_spec.md` 第 432 行明确写死：

> 按位写掩码 (`bwen` / `wr_mask` / `*_bwen`) | `WIDTH` | 每 bit 对应数据的 1 bit

### 1.2 问题

实际 SoC 设计中，上游模块经常使用更粗粒度的 mask（如 byte-mask、word-mask），而 vendor memory 的 BWEB 始终是 bit-level。需要在某一层完成 **mask 展开**（每个粗粒度 mask bit 复制为多个 bit-level mask bit）。

### 1.3 设计决策

- **展开位置**：L1 (`physical_wrapper`) 内部
- **L3/L2/L1 端口**：全部透传粗粒度 mask，不做变换
- **Vendor 连接**：L1 内部展开后连接 vendor cell 的 bit-level BWEB

```
用户端口           L2                L1 端口         L1 内部           Vendor
mask[MW-1:0] → pipeline透传 → bwen[TW-1:0] → 展开逻辑 → BWEB[lib_w-1:0]

MW  = DATA_WIDTH / mask_gran     (用户侧 mask 宽度)
TW  = col_count × lib_mask_width (含 tiling padding)
GRAN = lib_width / lib_mask_width (展开倍数)
```

---

## 2. 术语定义

| 术语 | 计算公式 | 含义 |
|------|---------|------|
| `lib_mask_width` | 配置项 | 每个 vendor cell 对应的 mask bit 数 |
| `mask_gran` | `lib_width / lib_mask_width` | 每个 mask bit 控制的 data bit 数 |
| `logical_mask_width` | `DATA_WIDTH / mask_gran` | 用户侧 mask 端口宽度（不含 padding） |
| `total_mask_width` | `col_count × lib_mask_width` | L1 mask 端口宽度（含 tiling padding） |
| `mask_pad_bits` | `total_mask_width - logical_mask_width` | 最后一列的 mask padding 位数 |

---

## 3. L1 展开逻辑规格

### 3.1 RTL 展开（per vendor cell instance）

```verilog
// 参数
localparam LIB_MASK_WIDTH = <lib_mask_width>;  // 每 cell 的 mask bit 数
localparam MASK_GRAN      = <mask_gran>;        // 展开倍数

// 从 bwen 端口切出当前 cell 的 mask slice
wire [LIB_MASK_WIDTH-1:0] bwen_c<col> = bwen[<col>*LIB_MASK_WIDTH +: LIB_MASK_WIDTH];

// 展开为 bit-level
wire [LIB_WIDTH-1:0] expanded_bwen_c<col>;
genvar g_bwen_c<col>;
generate
    for (g_bwen_c<col> = 0; g_bwen_c<col> < LIB_MASK_WIDTH;
         g_bwen_c<col> = g_bwen_c<col> + 1) begin : G_MASK_EXP_C<col>
        assign expanded_bwen_c<col>[g_bwen_c<col>*MASK_GRAN +: MASK_GRAN]
            = {MASK_GRAN{bwen_c<col>[g_bwen_c<col>]}};
    end
endgenerate

// 连接 vendor cell
VENDOR_CELL u_cell_r<row>_c<col> (
    ...
    .BWEB (~expanded_bwen_c<col>),  // 极性由 port_map 控制
    ...
);
```

### 3.2 向后兼容

当 `mask_gran == 1`（即 `lib_mask_width == lib_width`）时：
- `{1{bwen_c[g]}} == bwen_c[g]`
- 展开退化为直连，**零行为变更**
- 所有现有 test config（`lib_mask_width == lib_width`）无需修改

### 3.3 SIM 模型

当前 SIM 模型 (`physical_wrapper.v.j2:29`) 已正确支持：

```verilog
if (bwen[i / sim_mask_gran])
    sim_mem[addr][i] <= wdata[i];
```

无需修改。

---

## 4. 各层 Mask 端口宽度变更

### 4.1 L1 (physical_wrapper)

| 项目 | 变更前 | 变更后 |
|------|--------|--------|
| mask 端口宽度 | `total_mask_width` | `total_mask_width`（不变） |
| vendor 连接 | `bwen[hi:lo]` 直连 BWEB | `expanded_bwen` 连 BWEB |
| 新增逻辑 | — | mask 展开 generate block |

### 4.2 L2 (memory_wrapper)

| 项目 | 变更前 | 变更后 |
|------|--------|--------|
| mask 端口宽度 | `DATA_WIDTH` | `MASK_WIDTH`（= `logical_mask_width`） |
| Pipeline | `DATA_WIDTH` 位 pipe | `MASK_WIDTH` 位 pipe |
| ECC mask 展开 | 有（`G_MASK_EXP` generate） | **移除** |
| Mask padding | 仅 ECC 路径有 `pad_bwen` | 统一为 `{padding_1s, pipe_bwen}` |
| L1 连接 | `phy_bwen[DATA_WIDTH-1:0]` | `phy_bwen[TOTAL_MASK_WIDTH-1:0]` |

#### L2 Mask Padding（新增）

```verilog
// 非 ECC 路径，或 ECC 路径统一：
localparam MASK_WIDTH       = <logical_mask_width>;
localparam TOTAL_MASK_WIDTH = <total_mask_width>;

wire [TOTAL_MASK_WIDTH-1:0] padded_bwen =
    { {(TOTAL_MASK_WIDTH - MASK_WIDTH){1'b1}}, pipe_bwen };

// init 期间强制全写
wire [TOTAL_MASK_WIDTH-1:0] phy_bwen =
    init_ram_en ? {TOTAL_MASK_WIDTH{1'b1}} : padded_bwen;
```

#### L2 ECC Mask 展开移除

**变更前** (`memory_wrapper.v.j2:136-154`)：
```verilog
// 现有 ECC mask 展开 — 需要移除
wire [DATA_PAD_WIDTH-1:0] pad_bwen = { {pad_1s}, pipe_bwen };
wire [DATA_WITH_ECC_DW-1:0] ecc_bwen;
for (g = 0; g < ECC_SLICE_NUM; g++) begin : G_MASK_EXP
    assign ecc_bwen[g*N +: N] = { {parity_1s}, pad_bwen[g*K +: K] };
end
```

**变更后**：L2 不再做 ECC mask 展开。ECC parity 位的 mask 由 L1 的 `{GRAN{mask[g]}}` 自然覆盖。

### 4.3 L3 (bypass_wrapper)

mask 端口宽度从 `DATA_WIDTH` 改为 `MASK_WIDTH`，bypass 比较逻辑中 mask 相关的位宽同步修改。

### 4.4 TB (tb_gen)

mask 测试向量宽度从 `DATA_WIDTH` 改为 `MASK_WIDTH`。`mask_expect` 计算逻辑中需要按 `mask_gran` 展开来计算期望值。

---

## 5. ECC 交互分析

### 5.1 数据布局

Vendor cell 中 ECC 数据的物理布局（由 L2 ECC encoder 输出）：

```
[slice0_data(k)][slice0_parity(m)][slice1_data(k)][slice1_parity(m)]...
```

每个 ECC codeword 占 `n = k + m` 位，共 `ECC_SLICE_NUM` 个 slice。

### 5.2 正确展开条件

每个 mask bit 展开后覆盖 `mask_gran` 个连续 bit。要保证 ECC 正确性：

**每个 mask bit 必须覆盖完整的 ECC slice（data + parity 不可拆分）。**

- `mask[g]=1` → data 和 parity 一起写入 → ECC 一致 ✓
- `mask[g]=0` → data 和 parity 都不写 → 旧值 ECC 一致 ✓
- 若 mask bit 跨 slice 边界 → 部分 slice 的 parity 被写入但 data 未全写 → ECC 校验错误 ✗

### 5.3 对齐约束

```
mask_gran = lib_width / lib_mask_width
         = (ECC_SLICE_NUM × n) / lib_mask_width

对齐要求：mask_gran % n == 0
等价于：  ECC_SLICE_NUM % lib_mask_width == 0
```

即：**ECC slice 总数必须能被每个 cell 的 mask bit 数整除**，使得每个 mask bit 恰好覆盖整数个 slice。

### 5.4 示例

| 场景 | k | m | n | slices | lib_mask_width | mask_gran | 对齐 |
|------|---|---|---|--------|----------------|-----------|------|
| 1 mask/slice | 12 | 2 | 14 | 4 | 4 | 14 | ✓ (14%14==0) |
| 2 slices/mask | 8 | 4 | 12 | 8 | 4 | 24 | ✓ (24%12==0) |
| 跨 slice | 16 | 5 | 21 | 4 | 6 | 14 | ✗ (14%21≠0) |
| bit-level | 8 | 4 | 12 | 8 | 96 | 1 | ✓ (1 整除任何) |

---

## 6. Tiling 补位分析

### 6.1 Data Padding

多列拼接时，最后一列可能有未使用的 data bit。现有 `width_pad_bits` 处理，无需变更。

### 6.2 Mask Padding

```
例：DATA_WIDTH=48, lib_width=32 → col_count=2
mask_gran=8 → logical_mask_width=48/8=6, lib_mask_width=32/8=4

Col 0: 4 mask bits → 展开为 32 bit → 覆盖 data[31:0]    正常
Col 1: 4 mask bits → 展开为 32 bit → 覆盖 data[47:32]+pad
       其中 2 bit 对应有效 data，2 bit 对应 padding

L2 负责把 6-bit 用户 mask 补齐到 8-bit total_mask_width：
padded_bwen = { 2'b11, pipe_bwen[5:0] }  // padding mask = 1
```

padding mask 位 = 1 保证 padding data bit 被正常写入，避免 X 传播。**正确**。

### 6.3 校验规则

```python
# 已有
lib_width % lib_mask_width == 0  # mask_gran 为整数

# 新增
DATA_WIDTH % mask_gran == 0      # 用户 mask 宽度为整数
```

---

## 7. config_io.py 校验规则汇总

### 7.1 已有校验（保留）

| 规则 | 位置 | 条件 |
|------|------|------|
| mask 类型必须有 lib_mask_width | line 334-340 | `has_mask and lib_mask_width <= 0` → error |
| lib_width 整除 lib_mask_width | line 341-346 | `lib_width % lib_mask_width != 0` → error |

### 7.2 新增校验

| 规则 | 条件 | 错误信息 |
|------|------|---------|
| 用户 data_width 整除 mask_gran | `width % mask_gran != 0` | `width ({w}) must be evenly divisible by mask_gran ({g})` |
| ECC slice 对齐 | `ecc.enable and slice_count % lib_mask_width != 0` | `ECC slice_count ({s}) must be evenly divisible by lib_mask_width ({m})` |

---

## 8. 任务分解

### 前置关系

```
M1 (config_io 校验)
    ↓
M2 (memory_types_spec 规格更新)
    ↓
M3 (L1 展开实现) ←┐
    ↓              │
M4 (L2 mask 透传)  │
    ↓              │
M5 (L3 同步修改) ──┘ 并行于 M6
    ↓
M6 (TB 适配)
    ↓
M7 (单元测试)
    ↓
M8 (集成测试)
```

---

### M1 — `/py-designer`：config_io.py 校验增强

- **交付物**：`scripts/config_io.py` 修改
- **变更点**：
  1. `_validate_memory()` 中 mask 校验块后新增 2 条规则（见第 7 节）
  2. `TilingParams` 新增 `mask_gran: int = 1` 字段
  3. `calc_tiling()` 计算并填充 `mask_gran`
- **验收**：`uv run pytest scripts/tests/ -v` 全部通过（含新增校验的正反向测试）

### M2 — `/architect`：memory_types_spec 规格更新

- **交付物**：`doc/memory_types_spec.md` 修改
- **变更点**：
  1. 第 432 行位宽规则表：mask 端口位宽从 `WIDTH` 改为 `MASK_WIDTH = WIDTH / mask_gran`
  2. 新增 mask 粒度说明段落：定义 `mask_gran`、`lib_mask_width`、展开行为
  3. 各 mask 类型（1rwm/1r1wm/1r1wma/2rwm）的端口表更新 mask 位宽
  4. 第 6 节位宽配置规则汇总表更新
- **前置**：M1
- **验收**：文档 review 通过

### M3 — `/py-designer`：L1 physical_wrapper mask 展开

- **交付物**：
  - `scripts/physical_wrapper_gen.py` 修改
  - `scripts/templates/physical_wrapper.v.j2` 修改
- **变更点**：
  1. `build_context()` 中新增 `mask_gran` 和 `lib_mask_width` 到模板 context
  2. 模板 RTL section：当 `mask_gran > 1` 时，per cell 生成展开 generate block + `expanded_bwen` wire
  3. vendor cell 连接从 `bwen_slice` 改为 `expanded_bwen`（或 `mask_gran==1` 时退化直连）
  4. `_mask_slice_expr()` 可保留，仍用于从 `bwen` 端口切 slice
  5. SIM 模型无需修改（已支持 `sim_mask_gran`）
- **验证要点**：
  - `mask_gran == 1` 时生成结果与变更前完全一致（向后兼容）
  - `mask_gran > 1` 时 generate block 正确、命名唯一（含 row/col 后缀避免冲突）
- **前置**：M1
- **验收**：`uv run pytest scripts/tests/test_physical_wrapper_gen.py -v` 全部通过

### M4 — `/py-designer`：L2 memory_wrapper mask 透传

- **交付物**：
  - `scripts/memory_wrapper_gen.py` 修改
  - `scripts/templates/memory_wrapper.v.j2` 修改
- **变更点**：
  1. mask 端口宽度从 `DATA_WIDTH` 改为 `MASK_WIDTH`（新增 localparam）
  2. Pipeline 中 mask 信号宽度改为 `MASK_WIDTH`
  3. **移除** `G_MASK_EXP` generate block（ECC mask 展开）
  4. **移除** `pad_bwen` 的 ECC 路径逻辑
  5. **新增** 统一的 mask padding：`padded_bwen = { {pad_1s}, pipe_bwen }`
  6. init 期间 mask 宽度改为 `TOTAL_MASK_WIDTH`
  7. `phy_connect_lines` 中 mask 信号宽度更新
  8. `_make_write_path()` 中移除 `pad_mask_signal` / `ecc_bwen` / `genvar_mask` / `mask_gen_label` 字段
  9. 新增 `mask_width` / `total_mask_width` / `mask_gran` 到 context dict
- **影响的 Generator**：全部 4 个（SinglePort / DualPort / TDPGenerator / ROMGenerator），ROM 无 mask 不受影响
- **验证要点**：
  - `mask_gran == 1` 时 `MASK_WIDTH == DATA_WIDTH`，行为不变
  - `mask_gran > 1` 时 mask 端口宽度正确缩小
  - ECC + mask 场景：L2 不再展开，mask 直接透传
  - init_ram_en 期间 mask 为全 1（宽度正确）
- **前置**：M1, M3
- **验收**：`uv run pytest scripts/tests/test_memory_wrapper_gen.py -v` 全部通过

### M5 — `/py-designer`：L3 bypass_wrapper 同步修改

- **交付物**：
  - `scripts/bypass_wrapper_gen.py` 修改
  - `scripts/templates/bypass_wrapper.v.j2` 修改
- **变更点**：
  1. mask 端口宽度从 `DATA_WIDTH` 改为 `MASK_WIDTH`
  2. bypass 比较中 mask 相关的位选择逻辑按 `mask_gran` 调整
  3. 1r1wm 的 bypass output mux：按 mask bit 级别合并（每个 mask bit 控制 `mask_gran` 个 data bit）
- **前置**：M4
- **验收**：`uv run pytest scripts/tests/test_bypass_wrapper_gen.py -v` 全部通过

### M6 — `/py-designer`：TB 适配

- **交付物**：
  - `scripts/tb_gen.py` 修改
  - `scripts/tb_verilog.py` 修改
- **变更点**：
  1. mask 测试向量宽度从 `DATA_WIDTH` 改为 `MASK_WIDTH`
  2. `_build_mask_vectors()` 按 `MASK_WIDTH` 生成 mask pattern
  3. `_build_mask_expect()` 按 `mask_gran` 展开计算期望值（每个 mask bit 影响 `mask_gran` 个 data bit）
  4. TB Verilog 中 mask 信号声明宽度更新
  5. DUT 连接的 mask 端口位宽更新
- **前置**：M4
- **验收**：对 `config/mem_config.json` 全部实例生成 TB 无错误

### M7 — `/py-verifier`：单元测试增补

- **交付物**：各 `test_*.py` 增补测试用例
- **新增测试**：

| 测试文件 | 新增用例 | 覆盖点 |
|---------|---------|--------|
| `test_config_io.py` | `test_mask_gran_validation` | `width % mask_gran != 0` 报错 |
| `test_config_io.py` | `test_ecc_mask_alignment` | `slice_count % lib_mask_width != 0` 报错 |
| `test_config_io.py` | `test_mask_gran_valid_pass` | 合法配置通过校验 |
| `test_physical_wrapper_gen.py` | `test_mask_expansion_gen` | `mask_gran > 1` 时 generate block 生成正确 |
| `test_physical_wrapper_gen.py` | `test_mask_expansion_backward_compat` | `mask_gran == 1` 时输出不变 |
| `test_memory_wrapper_gen.py` | `test_mask_port_width` | mask 端口 = `MASK_WIDTH` 而非 `DATA_WIDTH` |
| `test_memory_wrapper_gen.py` | `test_no_ecc_mask_expand` | ECC + mask 时无 `G_MASK_EXP` |
| `test_memory_wrapper_gen.py` | `test_mask_padding` | padding mask = 1 |
| `test_tb_gen.py` | `test_coarse_mask_vectors` | mask 向量宽度 = `MASK_WIDTH` |

- **fixture 数据**：在 `scripts/tests/fixtures/` 下新增 `mask_expansion/` 子目录
- **前置**：M3, M4, M6
- **验收**：`uv run pytest scripts/tests/ -v` 全部通过

### M8 — `/rtl-verifier`：集成回归测试

- **交付物**：回归测试报告
- **需求**：
  1. `comprehensive_test_config.json` 新增 2-3 个 `mask_gran > 1` 的实例：
     - 非 ECC byte-mask（如 `lib_mask_width=4, lib_width=32`）
     - ECC + coarse mask（如 `lib_mask_width=4, ECC slices=4`）
     - 多列 tiling + coarse mask
  2. 全量生成 + 仿真 → 全部 PASS
  3. 现有实例（`mask_gran == 1`）回归无退化
- **前置**：M7
- **验收**：新旧实例全部仿真 PASS

---

## 9. 风险与注意事项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| L2 ECC mask 展开移除后遗漏 | ECC + mask 功能回退 | M8 包含 ECC + mask 回归用例 |
| `mask_gran == 1` 向后兼容 | 现有功能退化 | M3/M4 必须验证直连退化；M8 回归现有用例 |
| L3 bypass mask 位宽 | bypass 合并逻辑错误 | M5 需逐行审查 bypass output mux |
| TB mask_expect 计算 | 仿真假 PASS/FAIL | M6 需对同一组数据手算验证 |
| 多列 + ECC + coarse mask | 三者组合的 corner case | M8 新增覆盖此组合的实例 |

---

## 10. 验收标准

1. `uv run pytest scripts/tests/ -v` — 全部通过（含新增用例）
2. `uv run python scripts/mem_gen.py --full` — 现有 config 无错误
3. 新增 coarse-mask 实例 — 仿真 PASS
4. 现有 `mask_gran == 1` 实例 — 仿真无退化
5. `doc/memory_types_spec.md` — 更新并 review 通过
