# TB 生成模块设计文档

> 模块：`scripts/tb_gen.py` + `scripts/tb_verilog.py`
> 版本：0.2.0（Wave 2 重构后）
> 最后更新：2026-03-28

---

## 目录

- [1. 设计目标](#1-设计目标)
- [2. 生成逻辑：interface_type 派发](#2-生成逻辑interface_type-派发)
- [3. 生成文件结构](#3-生成文件结构)
- [4. Makefile 结构说明](#4-makefile-结构说明)
- [5. 激励文件格式](#5-激励文件格式)
- [6. 关键时序：Burst+Pipeline 读验证](#6-关键时序burstpipeline-读验证)
- [7. TDP 双向测试策略](#7-tdp-双向测试策略)
- [8. Mask 验证策略](#8-mask-验证策略)
- [9. ECC 验证](#9-ecc-验证)
- [10. 类型适配矩阵](#10-类型适配矩阵)
- [11. Python 模块接口](#11-python-模块接口)

---

## 1. 设计目标

为 `mem_config.json` 中每个 memory 实例自动生成：

1. **自检 Testbench** (`output/tb/tb_{top_name}.v`) — 激励驱动 + 自动比对
2. **激励文件** (`output/tb/{top_name}_*.hex`) — `$readmemh` 加载的测试向量
3. **仿真 Makefile** (`output/tb/Makefile`) — 覆盖所有实例，Verilator 两步构建

覆盖所有 9 种 interface_type，支持 ECC / Init / Mask / Pipeline / TDP 双向的组合验证。

---

## 2. 生成逻辑：interface_type 派发

### 2.1 Generator 类层次

```
TbGenerator (ABC)
├── SinglePortTbGen      — 1rw, 1rwm
├── DualPortTbGen        — 1r1w, 1r1wm, 1r1wa, 1r1wma
├── TrueDualPortTbGen    — 2rw, 2rwm
└── RomTbGen             — rom
```

派发依据 `base_type`（`interface_type.base_type`）：

```python
GENERATORS: dict[str, type[TbGenerator]] = {
    "single_port":    SinglePortTbGen,
    "dual_port":      DualPortTbGen,
    "true_dual_port": TrueDualPortTbGen,
    "rom":            RomTbGen,
}

def gen_tb(...):
    base_type = interface_type.base_type
    generator = GENERATORS[base_type]()
    ctx = generator.build_context(...)
    verilog = template.render(ctx)
```

### 2.2 DUT 层次适配

TB 始终实例化 `{top_name}_top`（最顶层模块），接口适配：

| 配置 | DUT | 端口前缀 |
|------|-----|---------|
| `enable_l2=false` | L1 physical_wrapper | 无前缀（L1 端口风格） |
| `enable_l2=true` | L2/L3 顶层 | `i_` / `o_` 前缀 |

`build_context()` 中通过 `is_l2 = mem_spec.enable_l2` 控制端口前缀，端口声明和 DUT 实例化均由 `tb_verilog.py` 函数生成。

---

## 3. 生成文件结构

```
output/
├── tb/                              — TB 相关文件（源码级，提交可复现）
│   ├── Makefile                     — Verilator 仿真管理（所有实例）
│   ├── tb_{top_name}.v              — 自检 Testbench
│   ├── {top_name}_wr_data.hex       — 写数据激励
│   ├── {top_name}_rd_expect.hex     — 期望读数据
│   ├── {top_name}_mask.hex          — Mask 激励（mask 类型）
│   ├── {top_name}_mask_expect.hex   — Mask 期望值（mask 类型）
│   ├── {top_name}_rom_init.hex      — ROM 初始数据（rom 类型）
│   └── {top_name}_b_wr_data.hex     — TDP B->A 写激励（TDP 类型）
│   └── {top_name}_b_rd_expect.hex   — TDP B->A 期望读数（TDP 类型）
└── sim/                             — 仿真产物（运行后生成，可安全删除）
    ├── obj_dir/                     — Verilator C++ 中间文件
    ├── {top_name}_sim               — 仿真可执行文件
    ├── {top_name}.log               — 仿真日志
    └── *.vcd / *.fst                — 波形文件（如有）
```

**设计原则**：
- `output/tb/` 下所有文件由工具生成，内容确定性，可重复生成覆盖
- `output/sim/` 是运行时产物，`make clean` 清除，用户可安全删除
- Makefile 使用 order-only prerequisite 确保 `sim/` 目录自动创建

---

## 4. Makefile 结构说明

单一 `output/tb/Makefile` 管理所有实例的仿真。

### 4.1 变量定义

```makefile
VERILATOR_ROOT ?= ...   # Verilator 安装根目录
VERILATOR      ?= ...   # verilator 可执行文件路径
MAKE_CMD       ?= ...   # make 工具（Windows: mingw32-make）
PYTHON3        ?= ...   # Python 解释器路径

TB_DIR  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
SIM_DIR := $(TB_DIR)../sim
```

所有路径变量均通过 `?=` 定义，用户可在调用时覆盖：
```bash
make sim VERILATOR_ROOT=/usr/share/verilator VERILATOR=verilator
```

### 4.2 主要 Target

| Target | 说明 |
|--------|------|
| `sim` | 默认目标，Verilator 两步构建并运行所有实例 |
| `sim-vcs` | VCS 仿真（预留 stub，待 CI 扩展） |
| `clean` | 删除 `$(SIM_DIR)/` 下所有文件（目录保留） |
| `$(SIM_DIR)` | order-only prerequisite，自动创建 sim 目录 |

### 4.3 Verilator 两步构建

每个实例对应两步：

```makefile
# Step 1: Verilate (生成 C++ + Makefile)
$(VERILATOR_ROOT) $(VERILATOR) \
    --cc --timing --exe --main -DSIM \
    -f $(TB_DIR)../rtl/filelist.f \
    $(TB_DIR)tb_{top_name}.v \
    --top-module tb_{top_name} \
    -o $(SIM_DIR)/{top_name}_sim \
    --Mdir $(SIM_DIR)/obj_dir

# Step 2: Build (C++ 编译链接)
PATH="$(MAKE_DIR):$$PATH" \
$(MAKE_CMD) -f $(SIM_DIR)/obj_dir/Vtb_{top_name}.mk \
    PYTHON3="$(PYTHON3)" \
    "LDFLAGS=-Wl,--undefined=main" \
    -j4

# Step 3: Run
$(SIM_DIR)/{top_name}_sim > $(SIM_DIR)/{top_name}.log 2>&1
grep -q "^PASS:" $(SIM_DIR)/{top_name}.log && echo "PASS: {top_name}" || echo "FAIL: {top_name}"
```

> Windows 注意：`verilated.mk` 硬编码 `PYTHON3 = python3`，需通过 `PYTHON3=` 参数覆盖。mingw 只有 `mingw32-make`，LDFLAGS 需加 `-Wl,--undefined=main` 强制提取 main 符号。

### 4.4 clean Target

```makefile
clean:
	@[ -d $(SIM_DIR) ] && rm -rf $(SIM_DIR)/* || true
```

使用 `|| true` 保证 `sim/` 目录不存在时不报错。仅删除目录内容，目录本身保留（Makefile 中用 order-only prerequisite 重建）。

---

## 5. 激励文件格式

### 5.1 标准写数据（`{top_name}_wr_data.hex`）

每行一个十六进制字，位宽 = `DATA_WIDTH`，共 `NUM_WRITE_VECTORS` 行。
向量数量：`min(RAM_DEPTH, 32)`。

数据 pattern（覆盖边界和交替值）：

| 索引 | 值 |
|------|-----|
| 0 | `0xA5A5...` 交替 pattern |
| 1 | `0x5A5A...` 反相 |
| 2 | `0xDEAD...` |
| 3 | `0xCAFE...` |
| 4..N-3 | `{addr, ~addr, ...}` 地址相关 |
| N-2 | `0xFFFF...` 全 1 |
| N-1 | `0x0000...` 全 0 |

### 5.2 TDP B->A 激励（`{top_name}_b_wr_data.hex`）

TDP（true_dual_port）类型额外生成 B 端写数据，为 A 端数据的逐位取反：

```python
b_wr_data[i] = wr_data[i] ^ ((1 << width) - 1)
```

取反策略确保 B->A 路径的读回值可与 A->B 路径明确区分，无需额外生成数据集。

### 5.3 Mask 激励（`{top_name}_mask.hex`）

8 种 mask pattern，覆盖全写、全保持、高低字节、交替位、nibble 等场景。`mask_expect.hex` 由 Python 预计算：

```python
expected[i] = (old_data[i] & ~mask[i]) | (new_data[i] & mask[i])
```

---

## 6. 关键时序：Burst+Pipeline 读验证

### 6.1 设计动机

旧方案（串行）：对每个读地址等待完整 `TOTAL_RD_LATENCY` 周期后再读下一个，效率低且无法验证流水线吞吐。

新方案（Burst+Pipeline）：连续发射所有读请求，单循环内完成发射和检查。

### 6.2 单循环结构

```verilog
// TOTAL_RD_LATENCY 个空泡 + NUM_READ_VECTORS 个有效检查
for (i = 0; i < NUM_READ_VECTORS + TOTAL_RD_LATENCY; i = i + 1) begin
    @(posedge clk); #1;
    // 发射阶段：前 NUM_READ_VECTORS 拍连续驱动读请求
    if (i < NUM_READ_VECTORS) begin
        {rd_port}_cen  = 1'b1;
        {rd_port}_wen  = 1'b0;  // (SP only)
        {rd_port}_addr = i[ADDR_WIDTH-1:0];
    end else begin
        {rd_port}_cen  = 1'b0;  // drain: 排空流水线
    end
    // 检查阶段：延迟 TOTAL_RD_LATENCY 后逐拍检查
    if (i >= TOTAL_RD_LATENCY) begin
        check_rdata(rd_expect_mem[i - TOTAL_RD_LATENCY], o_rdata, i - TOTAL_RD_LATENCY);
    end
end
```

### 6.3 时序图

```
时钟周期:  0  1  2  3  4  5  6  7  (TOTAL_RD_LATENCY=2, NUM_READ_VECTORS=4)
发射 addr: 0  1  2  3  -  -  -  -
CEN:       1  1  1  1  0  0  0  0
                              ↓  ↓  ↓  ↓
检查 idx:  -  -  -  -  -  -  0  1  2  3
           ←───── latency=2 ─────→
```

循环共 `NUM_READ_VECTORS + TOTAL_RD_LATENCY = 4 + 2 = 6` 拍，每个发射对应一个检查，验证流水线正确性。

---

## 7. TDP 双向测试策略

TrueDualPort (2rw / 2rwm) 支持双端口独立读写，需验证两条数据路径：

### 7.1 A 写 -> B 读（主路径）

1. Port A 写入 `wr_data_mem[]`（A5A5 等 pattern）
2. Port B 读取，burst+pipeline 检查，期望值 = `rd_expect_mem[]`

### 7.2 B 写 -> A 读（反向路径）

1. Port B 写入 `b_wr_data_mem[]`（A 端数据逐位取反）
2. Port A 读取，burst+pipeline 检查，期望值 = `b_rd_expect_mem[]`（= B 写数据）

```verilog
// TDP B->A 写相位
for (i = 0; i < NUM_WRITE_VECTORS; i = i + 1) begin
    @(posedge b_clk); #1;
    i_b_cen   = 1'b1;
    i_b_wen   = 1'b1;
    i_b_addr  = i[ADDR_WIDTH-1:0];
    i_b_wdata = b_wr_data_mem[i];
end

// TDP A 读检查相位（burst+pipeline）
for (i = 0; i < NUM_WRITE_VECTORS + TOTAL_RD_LATENCY; i = i + 1) begin
    @(posedge a_clk); #1;
    if (i < NUM_WRITE_VECTORS) begin
        i_a_cen  = 1'b1;
        i_a_wen  = 1'b0;
        i_a_addr = i[ADDR_WIDTH-1:0];
    end else begin
        i_a_cen  = 1'b0;
    end
    if (i >= TOTAL_RD_LATENCY) begin
        check_rdata(b_rd_expect_mem[i - TOTAL_RD_LATENCY], o_a_rdata, i - TOTAL_RD_LATENCY);
    end
end
```

逐位取反确保两条路径的数据不重叠，任何路径串扰都会导致 checker 报错。

---

## 8. Mask 验证策略

Mask 测试在基本写读之后执行，使用已知旧数据做 masked 覆写：

1. Mask 写：驱动 `{mask_port}` + 新数据，写入部分 bit
2. 读回：burst+pipeline 检查，期望值由 Python 预计算

```python
# Python 端预计算 mask_expect
old_data = wr_data[i % len(wr_data)]
new_data = mask_new_data[i]
mask_val = mask_patterns[i]
expected = (old_data & ~mask_val) | (new_data & mask_val)
```

Mask 位宽 = `MASK_WIDTH`（= `DATA_WIDTH / mask_gran`），TB 中声明：
```verilog
localparam MASK_WIDTH = {{ mask_width }};
reg [MASK_WIDTH-1:0] mask_mem [0:NUM_MASK_VECTORS-1];
```

---

## 9. ECC 验证

基本 TB 仅验证**正常路径**（无注错）：

```verilog
if (i >= TOTAL_RD_LATENCY) begin
    check_rdata(rd_expect_mem[i - TOTAL_RD_LATENCY], o_rdata, i - TOTAL_RD_LATENCY);
    if (o_ecc_correctable_valid !== 1'b0) begin
        $display("ERROR: unexpected correctable ECC error at check %0d", i - TOTAL_RD_LATENCY);
        errors = errors + 1;
    end
    if (o_ecc_uncorrectable_valid !== 1'b0) begin
        $display("ERROR: unexpected uncorrectable ECC error at check %0d", i - TOTAL_RD_LATENCY);
        errors = errors + 1;
    end
end
```

ECC 注错/纠错的深度验证（`i_ecc_err_insert`）由手写 TB 负责，不在自动生成范围内。

---

## 10. 类型适配矩阵

| feature | 1rw | 1rwm | 1r1w | 1r1wm | 1r1wa | 1r1wma | 2rw | 2rwm | rom |
|---------|:---:|:----:|:----:|:-----:|:-----:|:------:|:---:|:----:|:---:|
| Write phase | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| Burst+Pipeline read | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Mask phase | — | ✓ | — | ✓ | — | ✓ | — | ✓ | — |
| TDP B->A path | — | — | — | — | — | — | ✓ | ✓ | — |
| Init phase (L2) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| ECC check (L2+ECC) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Async clocks | — | — | — | — | ✓ | ✓ | — | — | — |
| Dual clocks (TDP) | — | — | — | — | — | — | ✓ | ✓ | — |

---

## 11. Python 模块接口

### 11.1 tb_gen.py 入口函数

```python
def gen_tb(
    mem_spec: MemorySpec,
    ecc_params: EccParams,
    interface_type: InterfaceType,
    top_name: str,
    phy_wrapper_name: str,
    tb_outdir: Path,
    sim_outdir: Path,
) -> None:
    """Generate TB Verilog + hex stimulus files for one memory instance."""

def gen_makefile(
    top_names: list[str],
    tb_outdir: Path,
) -> Path:
    """Generate Verilator simulation Makefile covering all instances.
    Called once after all instances are processed."""
```

### 11.2 tb_verilog.py Snippet 函数

`tb_verilog.py` 提供各阶段的 Verilog snippet 生成函数，均接受 `ctx: dict` 和端口前缀 `p: str`：

```python
# 各 phase snippet 函数
def sp_write_phase(ctx, p) -> str
def sp_read_check_phase(ctx, p) -> str      # burst+pipeline
def dp_write_phase(ctx, p) -> str
def dp_read_check_phase(ctx, p) -> str      # burst+pipeline
def tdp_write_phase(ctx, p) -> str          # Port A write
def tdp_read_check_phase(ctx, p) -> str     # Port B read, burst+pipeline
def tdp_b_write_phase(ctx, p) -> str        # Port B write (B->A path)
def tdp_a_read_check_phase(ctx, p) -> str   # Port A read (B->A path), burst+pipeline
def rom_read_check_phase(ctx, p) -> str     # burst+pipeline
def mask_write_phase(ctx, p) -> str
def mask_read_check_phase(ctx, p) -> str    # burst+pipeline

# ECC check snippet (可嵌套，支持 indent 参数)
def _ecc_check_snippet(port_prefix: str, indent: int = 8) -> str
```

### 11.3 Context Dict 关键字段

```python
ctx = {
    # TDP B->A 路径（非 TDP 类型设为 ""）
    "b_wr_hex_file":   str,   # "" or "{top_name}_b_wr_data.hex"
    "b_rd_hex_file":   str,   # "" or "{top_name}_b_rd_expect.hex"
    "b_write_phase":   str,   # "" or Verilog snippet
    "a_read_check_phase": str, # "" or Verilog snippet

    # 功能标志
    "is_l2":      bool,   # enable_l2 — 控制端口前缀
    "has_ecc":    bool,
    "has_mask":   bool,
    "has_init":   bool,
    "is_async":   bool,
    "is_rom":     bool,

    # 延迟
    "total_rd_latency": int,  # INPUT_PIPE + ram_rd_latency + ECC_PIPE + OUTPUT_PIPE

    # 向量数量
    "num_write_vectors": int,  # min(RAM_DEPTH, 32)
    "num_read_vectors":  int,  # = num_write_vectors
    "num_mask_vectors":  int,  # 8（固定）
}
```
