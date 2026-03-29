# sram_mem_gen

SRAM Memory Wrapper Verilog 代码生成器。根据 JSON 配置自动生成可综合的三层封装 RTL、自检 Testbench 及仿真 Makefile。

## 核心特性

- **三层可配置封装**：L1 物理拼接 → L2 ECC/Init FSM/Pipeline → L3 读写 Bypass
- **9 种接口类型**：单端口、双端口（同步/异步）、真双端口、ROM，各类型支持可选 bit-mask
- **ECC 支持**：基于 OpenTitan SECDED 自动生成编解码模块
- **自动 Tiling**：按列（位宽）× 行（深度）拼接 vendor cell，自动处理 padding
- **增量生成**：config hash 比对，未变更实例跳过，`--full` 强制全量重建
- **自检 TB 生成**：流水线吞吐验证、Mask 验证、TDP 双向路径验证
- **Vendor 无关**：核心生成器不绑定特定 vendor，通过 `vendor_port_map.json` 适配任意 SRAM cell

## 目录结构

```
sram_mem_gen/
├── config/                  # 配置文件
│   ├── mem_config.json      # 实例配置（定义需要生成哪些 SRAM）
│   ├── vendor_port_map.json # 端口映射（描述 vendor cell 接口）
│   └── demo/                # Demo 配置（通用信号名，开箱即用）
├── scripts/                 # 核心生成器
│   ├── mem_gen.py           # CLI 入口
│   ├── config_io.py         # 配置加载与校验
│   ├── physical_wrapper_gen.py  # L1 生成器
│   ├── memory_wrapper_gen.py    # L2 生成器
│   ├── bypass_wrapper_gen.py    # L3 生成器
│   ├── tb_gen.py            # Testbench 生成器
│   ├── ecc_calculator.py    # ECC 参数计算
│   ├── secded_gen.py        # OpenTitan SECDED 编解码生成（vendor 代码）
│   ├── templates/           # Jinja2 模板
│   ├── std/                 # 标准单元行为模型（可替换）
│   └── tests/               # pytest 单元测试
├── demo/                    # Vendor 行为仿真模型生成器（评测用）
│   └── vendor_model_gen.py
├── vendor/                  # Vendor cell 行为模型（仿真用，不纳入版本管理）
├── output/                  # 生成产物（RTL / TB / 仿真）
└── doc/                     # 设计文档
```

## 快速开始

### 环境要求

| 工具 | 版本 | 说明 |
|------|------|------|
| Python | 3.11+ | 通过 [uv](https://github.com/astral-sh/uv) 管理 |
| Verilator | 5.x | 仿真引擎（可选，仅仿真需要） |
| uv | 最新版 | Python 包管理器 |

### 安装

```bash
git clone <repo-url>
cd sram_mem_gen
uv sync
```

### 使用 Demo 配置体验完整流程

项目提供开箱即用的 Demo 配置，使用通用信号名，无需真实 vendor 库即可运行。

```bash
# Step 1: 生成 vendor 行为仿真模型（详见下方 "Vendor 行为模型生成器" 章节）
uv run python demo/vendor_model_gen.py \
  --config-dir config/demo \
  --vendor-dir vendor/demo

# Step 2: 运行生成器，产出 RTL + TB + Makefile
uv run python scripts/mem_gen.py \
  --config-dir config/demo \
  --output-dir output/demo

# Step 3: 运行仿真
make -C output/demo/tb
```

仿真输出末尾出现 `PASS` 即为验证通过：

```
[SRAM_TB] All 256 vectors PASS
$finish
```

### 使用自定义配置

```bash
# 默认读取 config/ 目录
uv run python scripts/mem_gen.py

# 指定配置文件
uv run python scripts/mem_gen.py --config-file config/my_config.json

# 强制全量重新生成
uv run python scripts/mem_gen.py --full

# 仅生成 RTL，跳过 TB
uv run python scripts/mem_gen.py --no-tb
```

## 三层封装架构

```
L3: bypass_wrapper      — 读写同地址 bypass（仅同步双端口 1r1w/1r1wm）
  └─ L2: memory_wrapper — ECC 编解码 + Init FSM + 多级 Pipeline
       └─ L1: physical_wrapper — Tiling（col × row），无 rst_n
            └─ N×M 个 vendor memory cell
```

每层独立启用，由 `mem_config.json` 中的 `enable_l2` / `enable_l3` 控制：

| 配置 | L1 后缀 | L2 后缀 | L3 后缀 |
|------|---------|---------|---------|
| `enable_l2=false` | `_top` | — | — |
| `enable_l2=true` | `_phy` | `_top` | — |
| `enable_l3=true` | `_phy` | `_mem` | `_top` |

## 类型体系

9 种 interface_type，命名规则 `<端口数><功能>[<修饰符>]`（m=mask, a=async）：

| 分类 | interface_type | 描述 |
|------|---------------|------|
| single_port | `1rw` | 单端口读写 |
| single_port | `1rwm` | 单端口读写 + bit-mask |
| dual_port | `1r1w` | 双端口同步 |
| dual_port | `1r1wm` | 双端口同步 + bit-mask |
| dual_port | `1r1wa` | 双端口异步（独立读写时钟） |
| dual_port | `1r1wma` | 双端口异步 + bit-mask |
| true_dual_port | `2rw` | 真双端口（A/B 端均可读写） |
| true_dual_port | `2rwm` | 真双端口 + bit-mask |
| rom | `rom` | 只读存储器 |

详细接口规格与时序图见 [doc/memory_types_spec.md](doc/memory_types_spec.md)。

## 配置说明

### mem_config.json — 实例配置

定义项目中需要生成的 SRAM 实例及其规格：

```json
{
  "project": "my_chip",
  "prefix": "mc",
  "memories": [
    {
      "name": "icache_data",
      "type": "1rw",
      "width": 64,
      "depth": 1024,
      "ram_rd_latency": 1,
      "input_pipe_stages": 0,
      "ecc_pipe_stages": 0,
      "output_pipe_stages": 0,
      "ecc": { "enable": false },
      "physical": {
        "lib_name": "SRAM_SP_256x32",
        "lib_width": 32,
        "lib_depth": 256
      },
      "enable_l2": true,
      "enable_l3": false
    }
  ]
}
```

### vendor_port_map.json — 端口映射

描述 vendor SRAM cell 的端口映射关系，工具内部统一使用 active-HIGH 极性。值带 `~` 前缀表示 vendor 端口为低有效，生成器会自动插入取反逻辑。

```json
{
  "vendor": "my_foundry",
  "lib_paths": ["vendor"],
  "lib_name_map": { "sp": "sp_std" },
  "lib_name_strip_suffixes": ["svt", "lvt"],
  "interface_types": {
    "1rw": {
      "base_type": "single_port",
      "has_mask": false,
      "port_map": {
        "clk": "CK", "cen": "~CSN", "wen": "~WEN",
        "addr": "A", "wdata": "D", "rdata": "Q"
      },
      "sub_types": [
        {
          "names": ["sp_std"],
          "const_ports": { "BWEB": "zeros", "MARGIN": 0 },
          "output_ports": ["SCAN_OUT"]
        }
      ]
    }
  }
}
```

详细配置参考见 [doc/quickstart.md](doc/quickstart.md)。

## Vendor 行为模型生成器

### 定位与用途

`demo/vendor_model_gen.py` 是一个**开发阶段评测工具**，用于在没有真实 vendor SRAM 库的情况下，根据 `vendor_port_map.json` 中的端口映射自动生成行为级 Verilog 仿真模型。

它的目的是让用户在获取真实 vendor 库之前就能完成以下工作：

- **功能评测**：验证生成器输出的 RTL 封装逻辑是否正确
- **流程验证**：跑通从配置 → 生成 → 仿真的完整工作流
- **接口调试**：确认 `vendor_port_map.json` 的端口映射配置是否正确

生成的模型覆盖全部 9 种 interface_type（1rw、1rwm、1r1w、1r1wm、1r1wa、1r1wma、2rw、2rwm、rom），包括 bit-mask 写逻辑和极性翻转，功能行为与真实 SRAM 一致。

### 使用方法

```bash
# 基本用法：读取 config/demo/ 配置，输出到 vendor/demo/
uv run python demo/vendor_model_gen.py \
  --config-dir config/demo \
  --vendor-dir vendor/demo

# 指定配置文件（如综合测试配置）
uv run python demo/vendor_model_gen.py \
  --config-dir config/demo \
  --config-file config/demo/comprehensive_mem_config.json \
  --vendor-dir vendor/demo

# 强制覆盖已有模型
uv run python demo/vendor_model_gen.py \
  --config-dir config/demo \
  --vendor-dir vendor/demo \
  --overwrite
```

### 生产环境替换

> **重要**：生成的 vendor 模型仅用于功能仿真评测，不反映真实 SRAM 的时序特性。在实际项目中，必须将其替换为厂商提供的行为级仿真模型。

替换步骤：

1. **获取 vendor 模型**：从厂商获取 SRAM cell 的 Verilog 行为仿真模型（通常随 memory compiler 提供）
2. **放置到 vendor/ 目录**：文件名必须与 `mem_config.json` 中 `physical.lib_name` 一致，例如 `vendor/SRAM_SP_256x32.v`
3. **配置搜索路径**：确认 `vendor_port_map.json` 中 `lib_paths` 指向正确目录
4. **配置端口映射**：在 `vendor_port_map.json` 中按厂商 cell 的实际端口名填写 `port_map` 和 `const_ports`
5. **重新生成并验证**：
   ```bash
   uv run python scripts/mem_gen.py
   make -C output/tb
   ```

替换后无需修改核心生成器代码——端口适配完全通过 `vendor_port_map.json` 配置驱动。

## 测试

```bash
# 运行全部单元测试
uv run pytest scripts/tests/ -v

# 运行特定测试模块
uv run pytest scripts/tests/test_physical_wrapper_gen.py -v
```

## 生成产物结构

```
output/
├── report.json              # 生成报告（实例状态、config hash）
├── rtl/
│   ├── filelist.f           # RTL 文件列表（供 EDA 工具使用）
│   ├── common/std/          # 标准单元行为模型
│   ├── *_physical_wrapper.v # L1 RTL
│   ├── *_memory_wrapper.v   # L2 RTL
│   └── *_top.v              # 顶层模块
├── tb/
│   ├── Makefile             # Verilator 仿真管理
│   ├── tb_*.v               # 自检 Testbench
│   └── *.hex                # 仿真激励数据
└── sim/                     # 仿真产物（Verilator 构建 + 波形 + 日志）
```

## 文档索引

| 文档 | 内容 |
|------|------|
| [doc/quickstart.md](doc/quickstart.md) | 快速上手（配置详解 + 完整流程） |
| [doc/architecture.md](doc/architecture.md) | 全局架构设计 |
| [doc/memory_types_spec.md](doc/memory_types_spec.md) | 9 种接口类型规格与时序图 |
| [doc/physical_wrapper_design.md](doc/physical_wrapper_design.md) | L1 设计：Tiling 算法、端口映射 |
| [doc/memory_wrapper_design.md](doc/memory_wrapper_design.md) | L2 设计：ECC、Init FSM、Pipeline |
| [doc/bypass_wrapper_design.md](doc/bypass_wrapper_design.md) | L3 设计：Bypass FIFO、Mask 合并 |
| [doc/tb_gen_design.md](doc/tb_gen_design.md) | TB 生成：测试策略、Makefile 工作流 |

## 第三方依赖

- **[Jinja2](https://jinja.palletsprojects.com/)**：模板引擎，用于 RTL/TB 代码生成
- **[secded_gen.py](https://github.com/lowRISC/opentitan)**（OpenTitan, Apache 2.0）：SECDED ECC 编解码 SystemVerilog 生成
- **[hjson](https://hjson.github.io/) / [Mako](https://www.makotemplates.org/)**：仅 secded_gen.py 内部使用

## License

本项目基于 [GNU General Public License v3.0](LICENSE) 开源。
