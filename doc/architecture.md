# sram_mem_gen — 全局架构文档

> 版本：0.2.0
> 最后更新：2026-03-28

---

## 目录

- [1. 项目定位](#1-项目定位)
- [2. 三层封装架构](#2-三层封装架构)
- [3. 类型体系](#3-类型体系)
- [4. 配置体系](#4-配置体系)
- [5. 代码生成流程](#5-代码生成流程)
- [6. 仿真验证流程](#6-仿真验证流程)
- [7. 第三方依赖](#7-第三方依赖)
- [8. 子模块文档索引](#8-子模块文档索引)

---

## 1. 项目定位

sram_mem_gen 是一个 **SRAM memory wrapper Verilog 代码生成器**。根据 JSON 配置，自动生成可综合的三层封装 RTL，并配套生成自检 Testbench 和仿真 Makefile。

**典型使用场景**：
- 用户已有 vendor SRAM 物理单元（lib_name，含行为仿真模型）
- 需要在物理单元之上增加 ECC、Init FSM、读写 Pipeline、bypass 等功能封装
- 需要为每个实例生成独立的 TB 进行基本功能验证

**工作流**：
```
用户提供 mem_config.json + vendor_port_map.json
    → python scripts/mem_gen.py
        → output/rtl/     (可综合 RTL + filelist.f)
        → output/tb/      (TB Verilog + hex 激励 + Makefile)
        → output/sim/     (仿真产物，运行后生成)
```

---

## 2. 三层封装架构

```
L3: bypass_wrapper      — 读写同地址 bypass（仅同步双端口）
  └─ L2: memory_wrapper — ECC + Init FSM + Pipeline
       └─ L1: physical_wrapper — Tiling (col×row)，无 rst_n
            └─ N×M 个 vendor memory 物理单元
```

每层可独立启用，由 `mem_config.json` 中的 `enable_l2` / `enable_l3` 控制。模块命名规则：

| 配置 | L1 | L2 | L3 |
|------|----|----|-----|
| `enable_l2=false` | `{top_name}_top` | — | — |
| `enable_l2=true, enable_l3=false` | `{top_name}_phy` | `{top_name}_top` | — |
| `enable_l2=true, enable_l3=true` | `{top_name}_phy` | `{top_name}_mem` | `{top_name}_top` |

TB 始终实例化 `{top_name}_top`（最顶层模块）。

### 2.1 L1 — physical_wrapper

**职责**：物理拼接（Tiling），处理 vendor cell 位宽/深度与用户需求不整除时的 padding。

- 纯组合/时序逻辑，**无 rst_n**（vendor cell 无复位接口）
- 按列（data 位宽方向）× 行（深度方向）排列 vendor cell
- 列 padding：MSB 补充哑 cell；行 padding：高地址解码屏蔽
- 端口统一 active-HIGH，内部按 `vendor_port_map.json` 做极性翻转

详见 [physical_wrapper_design.md](physical_wrapper_design.md)

### 2.2 L2 — memory_wrapper

**职责**：ECC 编解码 + 初始化 FSM + 多级 Pipeline。

- **ECC**：通过 OpenTitan secded_gen 生成 SECDED 编解码模块
- **Init FSM**：上电复位后对全部地址写 `init_value`，完成后拉高 `o_init_done`
- **Pipeline**：输入/ECC/输出三级可配置流水线，总读延迟 = `input_pipe + ram_rd_latency + ecc_pipe + output_pipe`
- 端口带 `i_` / `o_` 前缀，低有效复位 `i_rst_n`

详见 [memory_wrapper_design.md](memory_wrapper_design.md)

### 2.3 L3 — bypass_wrapper

**职责**：读写同地址 bypass，解决同周期写后读透传问题。

- 仅支持同步双端口（1r1w、1r1wm）
- bypass FIFO 深度 = 总读延迟，按位 mask 选择 bypass 数据
- L3 输出端口继承 L2 接口（加 `i_bypass_en` 控制开关）

详见 [bypass_wrapper_design.md](bypass_wrapper_design.md)

### 2.4 ECC 与 Mask 的对齐约束

当 ECC + coarse mask 同时启用时，ECC codeword 需按 mask 粒度对齐：

```
padded_n = ceil(n / mask_gran) * mask_gran
mask_gran = lib_width / lib_mask_width
```

对齐在 `mem_gen.py` 中计算，传递给 L1 tiling 和 L2 mask 生成。

详见 [mask_expansion_design.md](mask_expansion_design.md)

---

## 3. 类型体系

9 种 interface_type，命名规则 `<端口数><功能>[<修饰符>]`（m=mask, a=async）：

| 分类 | interface_type | 描述 |
|------|---------------|------|
| single_port | `1rw` | 单端口读写 |
| single_port | `1rwm` | 单端口读写 + mask |
| dual_port | `1r1w` | 双端口同步（分离读写时钟） |
| dual_port | `1r1wm` | 双端口同步 + mask |
| dual_port | `1r1wa` | 双端口异步（独立读写时钟） |
| dual_port | `1r1wma` | 双端口异步 + mask |
| true_dual_port | `2rw` | 真双端口（A/B 端均可读写） |
| true_dual_port | `2rwm` | 真双端口 + mask |
| rom | `rom` | 只读存储器 |

各类型的详细接口定义、时序图（WaveDrom）及新类型添加指南见 [memory_types_spec.md](memory_types_spec.md)。

---

## 4. 配置体系

### 4.1 mem_config.json — 实例配置

用户主配置文件，定义项目元数据和每个 memory 实例。

```json
{
  "project": "my_project",
  "prefix": "prj",
  "memories": [
    {
      "name": "cache",
      "type": "1rw",
      "width": 32,
      "depth": 256,
      "ecc": { "enabled": true },
      "enable_l2": true,
      "enable_l3": false,
      "ram_rd_latency": 1,
      "input_pipe_stages": 0,
      "output_pipe_stages": 1,
      "physical": {
        "lib_name": "SRAM_SP_32x256",
        "lib_width": 32,
        "lib_depth": 256,
        "lib_mask_width": 0
      }
    }
  ]
}
```

顶层名称由工具自动生成：`{prefix}[_{name}]_RAM_{type}_{width}x{depth}`

### 4.2 vendor_port_map.json — 端口映射

描述 vendor SRAM cell 的端口映射关系，与实际 vendor 名称解耦（已脱敏）。

**两层分类结构**：
```
interface_type
  └─ sub_types[]
       ├── names[]         (lib_name 前缀匹配)
       ├── port_map        (功能端口 → vendor 端口名)
       ├── const_ports     (需 tie-off 的端口)
       └── output_ports    (vendor 输出端口)
```

**极性编码**：port_map 值带 `~` 前缀表示取反。Wrapper 内部统一 active-HIGH，在 L1 端口连接处做极性处理。

### 4.3 增量生成

`mem_gen.py` 默认为增量模式：对每个实例计算 `config_hash`，与上次 `report.json` 对比，未变更的实例跳过生成。使用 `--full` 强制全量重建。

---

## 5. 代码生成流程

```
mem_gen.py (CLI 入口)
├── ConfigLoader.load()          — 加载 mem_config.json + vendor_port_map.json
├── VendorLibChecker.verify()    — 检查 vendor cell 文件存在
├── _copy_std_cells()            — 复制 std/ 行为模型到 output/rtl/common/std/
│
├── for each memory instance:
│   ├── compute_config_hash()    — 增量判断
│   ├── PhysicalWrapperGenerator.calc_tiling()  — Tiling 计算
│   ├── EccCalculator.calc_params()             — ECC 参数
│   ├── gen_physical_wrapper()   — L1 RTL 生成
│   ├── gen_memory_wrapper()     — L2 RTL 生成 (if enable_l2)
│   ├── gen_bypass_wrapper()     — L3 RTL 生成 (if enable_l3)
│   └── gen_tb()                 — TB + hex + Makefile (if not --no-tb)
│
├── _write_filelist()            — output/rtl/filelist.f
├── gen_makefile()               — output/tb/Makefile
└── ReportWriter.write()         — output/report.json
```

各层生成器采用 **ABC + base_type dispatch** 模式：

```python
GENERATORS: dict[str, type[PhysicalWrapperGenerator]] = {
    "single_port":    SinglePortGenerator,
    "dual_port":      DualPortGenerator,
    "true_dual_port": TrueDualPortGenerator,
    "rom":            RomGenerator,
}
```

Jinja2 模板位于 `scripts/templates/`，使用 `StrictUndefined` 确保所有变量均已定义。

---

## 6. 仿真验证流程

### 6.1 文件布局

```
output/
├── rtl/
│   ├── filelist.f              — 所有 RTL 文件列表（相对路径）
│   ├── common/
│   │   ├── data_syncn.v        — 同步 pipeline 寄存器
│   │   └── std/                — std 行为模型（std_dffe.v 等）
│   └── {instance}/             — 各实例 RTL
│       ├── {top_name}_phy.v
│       ├── {top_name}_top.v
│       └── {ecc_enc/dec}.sv
├── tb/
│   ├── Makefile                — Verilator 仿真管理（所有实例）
│   ├── tb_{top_name}.v         — 自检 Testbench
│   ├── {top_name}_wr_data.hex  — 写数据激励
│   ├── {top_name}_rd_expect.hex — 期望读数据
│   ├── {top_name}_mask.hex      — Mask 激励（mask 类型）
│   └── {top_name}_b_wr_data.hex — TDP B->A 激励（TDP 类型）
└── sim/                        — 仿真产物（自动创建，可安全删除）
    ├── obj_dir/                — Verilator C++ 中间文件
    ├── *.vcd / *.fst           — 波形文件
    └── *.log                   — 仿真日志
```

### 6.2 Makefile 工作流

```bash
cd output/tb/

make sim          # Verilator 两步构建 + 运行所有实例仿真
make sim-vcs      # VCS 仿真（stub，待扩展）
make clean        # 清除 output/sim/ 下所有产物（目录保留）
```

`make sim` 流程（每个实例）：
```
Step 1 (Verilate): verilator_bin.exe --cc --timing --exe ... → obj_dir/
Step 2 (Build):    mingw32-make.exe -f obj_dir/V{top}.mk PYTHON3=... LDFLAGS=...
Step 3 (Run):      $(SIM_DIR)/{top_sim} > $(SIM_DIR)/{top_name}.log
```

`sim/` 目录使用 order-only prerequisite 自动创建，用户删除后 `make sim` 可自动恢复。

### 6.3 仿真验证策略

详见 [tb_gen_design.md](tb_gen_design.md)，关键设计：

- **Burst+Pipeline 读验证**：单循环 `i < NUM_READ_VECTORS + TOTAL_RD_LATENCY`，`i < NUM_READ_VECTORS` 时发射请求，`i >= TOTAL_RD_LATENCY` 时检查结果，验证流水线吞吐
- **TDP 双向测试**：A 写 B 读 + B 写 A 读（数据取反，可区分两条路径）
- **Mask 验证**：Python 预计算 mask 期望值，Verilog 侧仅做比对

---

## 7. 第三方依赖

### 7.1 secded_gen.py（OpenTitan，Apache 2.0）

**来源**：[OpenTitan 项目](https://github.com/lowRISC/opentitan)，Apache License 2.0

**位置**：`scripts/secded_gen.py` + `scripts/basegen/`

**用途**：根据数据位宽自动生成 SECDED（Single-Error Correction, Double-Error Detection）ECC 编解码 SystemVerilog 模块。

**使用方式**：
```python
from ecc_calculator import EccCalculator
calc = EccCalculator()
ecc_params = calc.calc_params(data_width=32, ecc_config={"enabled": True})
modules = calc.generate_modules(ecc_config, prefix, output_dir)
# 生成: {prefix}_ecc_enc_{k}_{n}.sv, {prefix}_ecc_dec_{k}_{n}.sv
```

**不修改原则**：`secded_gen.py` 和 `basegen/` 作为 vendor 代码整体引入，不做修改。如需升级，整体替换。

**依赖**：`hjson`、`mako`（仅 secded_gen.py 使用，通过 `uv` 管理）。

### 7.2 data_syncn.v（std 行为模型）

可配置深度的同步 pipeline 寄存器，用于 L2 read pipeline 的行为仿真。

**接口**：
```verilog
module data_syncn #(
    parameter DATA_WIDTH  = 1,
    parameter NUM_FLOPS   = 1,
    parameter RESET_VALUE = {DATA_WIDTH{1'b0}}
)(
    input                    clk,
    input                    reset_n,
    input  [DATA_WIDTH-1:0]  data_in,
    output [DATA_WIDTH-1:0]  data_out_sync
);
```

项目内置版本位于 `scripts/std/data_syncn.v`，用户可替换为自有实现。详见 [scripts/std/README.md](../scripts/std/README.md)。

---

## 8. 子模块文档索引

| 文档 | 内容 |
|------|------|
| [user_manual.md](user_manual.md) | 用户使用手册：安装、快速上手、配置参考、CLI 参数 |
| [memory_types_spec.md](memory_types_spec.md) | 9 种 interface_type 接口定义、时序图、新类型添加指南 |
| [physical_wrapper_design.md](physical_wrapper_design.md) | L1 physical_wrapper 设计：Tiling 算法、端口映射、Jinja2 模板 |
| [memory_wrapper_design.md](memory_wrapper_design.md) | L2 memory_wrapper 设计：ECC、Init FSM、Pipeline |
| [bypass_wrapper_design.md](bypass_wrapper_design.md) | L3 bypass_wrapper 设计：bypass FIFO、mask 合并 |
| [tb_gen_design.md](tb_gen_design.md) | TB 生成模块设计：dispatch、文件结构、Makefile、测试策略 |
| [mask_expansion_design.md](mask_expansion_design.md) | ECC + coarse mask 对齐设计 |
| [scripts/std/README.md](../scripts/std/README.md) | std 行为模型说明 + data_syncn 替换指南 |
