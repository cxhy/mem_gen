# sram_mem_gen 使用手册

> 版本：0.1.0
> 最后更新：2026-03-28

---

## 目录

- [1. 项目概述](#1-项目概述)
- [2. 部署与安装](#2-部署与安装)
- [3. 快速上手](#3-快速上手)
- [4. 命令行参数](#4-命令行参数)
- [5. 用户配置文件 mem_config.json](#5-用户配置文件-mem_configjson)
- [6. Vendor 配置文件 vendor_port_map.json](#6-vendor-配置文件-vendor_port_mapjson)
- [7. 类型体系](#7-类型体系)
- [8. ECC 配置](#8-ecc-配置)
- [9. 三层封装架构](#9-三层封装架构)
- [10. 输出产物](#10-输出产物)
- [11. 增量生成](#11-增量生成)
- [12. 仿真验证](#12-仿真验证)
- [13. 版本管理](#13-版本管理)
- [14. 常见问题](#14-常见问题)

---

## 1. 项目概述

sram_mem_gen 是一个 SRAM memory wrapper Verilog 生成器。根据 JSON 配置文件，自动生成三层封装的 Verilog RTL 代码：

- **L1 (physical_wrapper)** — 物理 cell 拼接（列×行 Tiling），处理位宽/深度不整除时的 padding
- **L2 (memory_wrapper)** — ECC 编解码 + Init FSM + 流水线（Input/ECC/Output Pipeline）
- **L3 (bypass_wrapper)** — 读写同地址 bypass（仅同步双端口类型）

同时生成 Testbench、仿真脚本、filelist.f 和生成报告。

---

## 2. 部署与安装

### 2.1 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Linux (x86_64) |
| Python | >= 3.11 |
| 仿真器（可选） | Icarus Verilog (iverilog) 或 Verilator |

### 2.2 获取源码

```bash
# 假设已通过内网传输获得源码包
tar xzf sram_mem_gen-0.1.0.tar.gz
cd sram_mem_gen
```

### 2.3 安装 Python 依赖

在封闭 Linux 环境下，使用 pip 安装：

```bash
# 确认 Python 版本 >= 3.11
python3 --version

# 建议使用虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装运行时依赖
pip install hjson>=3.1.0 jinja2>=3.1.6 mako>=1.3.10

# 安装开发依赖（仅运行测试时需要）
pip install pytest>=9.0.2
```

> **离线环境**：如果无法联网，需预先在有网环境下载 wheel 包：
> ```bash
> # 在有网环境：
> pip download hjson jinja2 mako pytest -d ./packages/
>
> # 将 packages/ 目录拷贝到目标机器后：
> pip install --no-index --find-links=./packages/ hjson jinja2 mako
> ```

### 2.4 验证安装

```bash
# 运行单元测试（可选，验证安装正确性）
PYTHONPATH=scripts:scripts/tests python3 -m pytest scripts/tests/ -v
```

### 2.5 Vendor Cell 文件准备

将 vendor 提供的 SRAM 行为仿真模型（`.v` 或 `.sv` 文件）放入 `vendor/` 目录。文件名必须与 `mem_config.json` 中 `physical.lib_name` 字段一致：

```
vendor/
├── ts5n7a256x32ulvt1024.v     # lib_name = "ts5n7a256x32ulvt1024"
├── ts6n7a256x32svt512.v       # lib_name = "ts6n7a256x32svt512"
└── ...
```

> `lib_paths` 字段控制搜索路径，详见 [6.2 顶层字段](#62-顶层字段)。

---

## 3. 快速上手

### 3.1 最小配置示例

创建 `config/mem_config.json`：

```json
{
  "project": "my_chip",
  "prefix": "mc",
  "memories": [
    {
      "name": "icache_data",
      "type": "1rw",
      "width": 128,
      "depth": 256,
      "ram_rd_latency": 1,
      "input_pipe_stages": 0,
      "ecc_pipe_stages": 0,
      "output_pipe_stages": 0,
      "ecc": { "enable": false },
      "physical": {
        "lib_name": "ts5n7a256x64ulvt1024",
        "lib_width": 64,
        "lib_depth": 256
      }
    }
  ]
}
```

### 3.2 运行生成

```bash
python3 scripts/mem_gen.py
```

### 3.3 查看输出

```
output/
├── report.json           # 生成报告
├── rtl/
│   ├── filelist.f         # RTL 文件列表（可直接用于 EDA 工具）
│   ├── common/            # 公共模块
│   └── mc_icache_data_RAM_1rw_128x256_top.v
├── tb/
│   ├── tb_mc_icache_data_RAM_1rw_128x256.v
│   ├── mc_icache_data_RAM_1rw_128x256.hex  # 激励数据
│   └── Makefile           # 仿真入口（所有实例）
└── sim/                   # 仿真运行产物（make 执行后生成）
```

---

## 4. 命令行参数

```
python3 scripts/mem_gen.py [OPTIONS]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--config-dir DIR` | `config/` | 配置目录，包含 `vendor_port_map.json`（及默认 `mem_config.json`） |
| `--config-file FILE` | `config/mem_config.json` | 指定 mem_config 文件路径（覆盖默认） |
| `--output-dir DIR` | `output/` | 输出目录 |
| `--full` | 关闭 | 全量重新生成（忽略增量缓存） |
| `--no-tb` | 关闭 | 跳过 Testbench 生成 |

### 使用示例

```bash
# 使用自定义配置
python3 scripts/mem_gen.py --config-file my_project/sram_config.json

# 全量生成到指定目录
python3 scripts/mem_gen.py --output-dir build/sram --full

# 只生成 RTL，不生成 TB
python3 scripts/mem_gen.py --no-tb
```

---

## 5. 用户配置文件 mem_config.json

### 5.1 顶层结构

```json
{
  "project": "项目名称",
  "prefix": "模块名前缀",
  "memories": [ ... ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project` | string | 是 | 项目名称，写入 report.json |
| `prefix` | string | 是 | 所有生成模块名的前缀。如 `"mc"` 则模块名为 `mc_xxx_RAM_...` |
| `memories` | array | 是 | 内存实例定义数组，每个元素定义一个 SRAM 实例 |

### 5.2 memories 数组 — 每个实例的字段

#### 基本字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `name` | string | 是 | — | 实例名称，用于生成模块名。建议 snake_case，如 `"icache_data"` |
| `type` | string | 是 | — | 接口类型，必须是 `vendor_port_map.json` 中 `interface_types` 的 key。可选值见 [7. 类型体系](#7-类型体系) |
| `width` | int | 是 | — | 用户数据位宽（bit） |
| `depth` | int | 是 | — | 用户地址深度（word 数） |

#### 延时参数

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `ram_rd_latency` | int | 是 | — | SRAM cell 本身的读延时（通常为 1） |
| `input_pipe_stages` | int | 是 | — | 输入流水线级数（L2 层，0 = 无流水） |
| `ecc_pipe_stages` | int | 否 | 0 | ECC 编解码流水线级数（仅 ECC 启用时有效） |
| `output_pipe_stages` | int | 是 | — | 输出流水线级数（L2 层） |

> 总读延时 = `input_pipe_stages` + `ram_rd_latency` + `ecc_pipe_stages` + `output_pipe_stages`

#### 层级控制

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `enable_l2` | bool | 否 | `true` | 是否生成 L2 (memory_wrapper)。设为 `false` 时仅生成 L1 (physical_wrapper)，流水线/ECC/Init 均不生效 |
| `enable_l3` | bool | 否 | `false` | 是否生成 L3 (bypass_wrapper)。**约束**：需 `enable_l2=true` 且 `type` 为同步双端口（`1r1w` 或 `1r1wm`） |
| `output_dir` | string | 否 | `""` | 输出子目录（相对于 `rtl/`），如 `"subsys_a/sram"`。为空时输出到 `rtl/` 根目录 |

#### ecc 对象

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `enable` | bool | 否 | `false` | 是否启用 ECC |
| `code_type` | string | ECC 启用时必填 | — | ECC 编码类型。可选值：`"hamming"`, `"hsiao"` |
| `data_bits_per_slice` | int | ECC 启用时必填 | — | 每个 ECC slice 的数据位宽 `k` |
| `ecc_bits_per_slice` | int | ECC 启用时必填 | — | 每个 ECC slice 的校验位宽 `m`。必须 >= 该 `k` 的最小校验位数 |
| `seed` | int\|null | 否 | `null` | ECC 矩阵随机种子。`null` 使用默认种子，指定整数确保生成可复现 |
| `detailed_report` | bool | 否 | `false` | 是否输出 ECC syndrome 详细报告端口 |

> 当 `enable` 为 `false` 时，只需写 `"ecc": { "enable": false }`，其余字段可省略。

#### physical 对象

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `sub_type` | string | **可选** | 自动推导 | Vendor cell 家族标识。省略时从 `lib_name` 自动推导（需 `vendor_port_map.json` 配置 `lib_name_map`）。显式提供时以用户值为准 |
| `lib_name` | string | 是 | — | Vendor cell 名称。必须与 `vendor/` 目录下的文件名（不含扩展名）一致。如 `"ts5n7a256x32ulvt1024"` |
| `lib_width` | int | 是 | — | 单个 vendor cell 的数据位宽 |
| `lib_depth` | int | 是 | — | 单个 vendor cell 的地址深度 |
| `lib_mask_width` | int | mask 类型必填 | 0 | Vendor cell 的 mask 位宽。当 `type` 为 mask 类型（`1rwm`/`1r1wm`/`1r1wma`/`2rwm`）时必须 > 0。`lib_width` 必须能被 `lib_mask_width` 整除 |

> **Mask 粒度**：`mask_gran = lib_width / lib_mask_width`。例如 `lib_width=32, lib_mask_width=4` 表示 byte-mask（每 8 bit 一个 mask bit）；`lib_mask_width=32` 表示 bit-mask。

> **sub_type 自动推导**：当省略 `sub_type` 时，系统将 `lib_name` 转小写、剥离电压后缀（ulvt/svt/lvt），然后在 `vendor_port_map.json` 的 `lib_name_map` 中查找最长前缀匹配。例如 `"sp_a256x32ulvt1024"` → 剥离 `ulvt` → `"sp_a256x321024"` → 匹配前缀 `"sp_a"` → 推导为 `"1prf"`。

### 5.3 完整示例

```json
{
  "project": "my_soc",
  "prefix": "soc",
  "memories": [
    {
      "name": "l1_icache",
      "type": "1rw",
      "width": 128,
      "depth": 512,
      "ram_rd_latency": 1,
      "input_pipe_stages": 1,
      "ecc_pipe_stages": 0,
      "output_pipe_stages": 1,
      "ecc": { "enable": false },
      "physical": {
        "lib_name": "ts5n7a256x64ulvt1024",
        "lib_width": 64,
        "lib_depth": 256
      },
      "enable_l2": true
    },
    {
      "name": "l2_tag",
      "type": "1r1wm",
      "width": 64,
      "depth": 256,
      "ram_rd_latency": 1,
      "input_pipe_stages": 0,
      "ecc_pipe_stages": 1,
      "output_pipe_stages": 0,
      "ecc": {
        "enable": true,
        "code_type": "hamming",
        "data_bits_per_slice": 102,
        "ecc_bits_per_slice": 8,
        "seed": 42,
        "detailed_report": true
      },
      "physical": {
        "sub_type": "uhd2prf",
        "lib_name": "ts6n7b256x110svt512",
        "lib_width": 110,
        "lib_depth": 256,
        "lib_mask_width": 110
      },
      "enable_l2": true,
      "enable_l3": true
    }
  ]
}
```

### 5.4 生成的模块命名规则

模块名格式：`{prefix}[_{name}]_RAM_{type}_{width}x{depth}`

各层后缀：

| 启用层级 | L1 后缀 | L2 后缀 | L3 后缀 |
|----------|---------|---------|---------|
| 仅 L1 | `_top` | — | — |
| L1 + L2 | `_phy` | `_top` | — |
| L1 + L2 + L3 | `_phy` | `_mem` | `_top` |

示例：`prefix="soc"`, `name="l1_icache"`, `type="1rw"`, `width=128`, `depth=512`
- 仅 L1：`soc_l1_icache_RAM_1rw_128x512_top`
- L1+L2：`soc_l1_icache_RAM_1rw_128x512_phy` + `soc_l1_icache_RAM_1rw_128x512_top`

---

## 6. Vendor 配置文件 vendor_port_map.json

此文件描述 vendor SRAM cell 的端口映射和常量连接。**一般不需要用户修改**，除非更换 vendor 或添加新的 cell 家族。

### 6.1 文件位置

默认路径：`config/vendor_port_map.json`（与 `--config-dir` 同目录）。

### 6.2 顶层字段

```json
{
  "vendor": "tsmc",
  "lib_paths": ["vendor"],
  "lib_name_map": { ... },
  "lib_name_strip_suffixes": ["ulvt", "svt", "lvt"],
  "interface_types": { ... }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `vendor` | string | Vendor 标识符，写入报告。如 `"example"` |
| `lib_paths` | array[string] | Vendor cell 文件搜索路径列表。相对路径相对于项目根目录。系统在这些目录中查找 `{lib_name}.v` 或 `{lib_name}.sv` 文件 |
| `lib_name_map` | object | lib_name 前缀 → sub_type 映射表。用于从 `lib_name` 自动推导 `sub_type`。key 为 lib_name 的前缀（小写），value 为对应的 sub_type 名称 |
| `lib_name_strip_suffixes` | array[string] | 推导 sub_type 前需从 lib_name 中剥离的电压后缀。如 `["ulvt", "svt", "lvt"]` |
| `interface_types` | object | 接口类型定义，key 为类型名（如 `"1rw"`），value 为接口定义对象 |

### 6.3 lib_name_map 详解

此映射表实现 `lib_name` → `sub_type` 的自动推导。推导算法：

1. 将 `lib_name` 转为小写
2. 依次从 `lib_name_strip_suffixes` 中移除所有匹配的电压后缀
3. 在 `lib_name_map` 中查找**最长匹配前缀**，返回对应的 sub_type

```json
"lib_name_map": {
    "sp_a":    "1prf",         // 标准单端口 variant A
    "sp_b":    "uhd1prf",      // UHD 单端口
    "sp_sb":   "spsbsram",     // SP small-bit SRAM
    "sp_uhd":  "uhdspsram",    // UHD SP SRAM
    "sp_mb":   "spmbsram",     // SP multi-bit SRAM
    "sp_hs":   "hsspsram",     // 高速 SP SRAM
    "dp_a":    "2prf",         // 标准双端口
    "dp_b":    "uhd2prf",      // UHD 双端口
    "tdp_a":   "dpsram",       // 真双端口
    "sp_l1":   "l1cache",      // L1 cache 专用
    "sp_hdmb": "hdspmbsram",   // HD SP multi-bit
    "sp_hdsb": "hdspsbsram",   // HD SP small-bit
    "rom_a":   "rom"           // ROM
}
```

> 如果 `lib_name_map` 为空或不存在，则 `physical.sub_type` 字段变为**必填**。

### 6.4 interface_types 详解

每个 interface_type 定义了一类 SRAM 的接口映射：

```json
"1rw": {
    "base_type": "single_port",
    "has_mask": false,
    "port_map": { ... },
    "sub_types": [ ... ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `base_type` | string | 基础类型。可选值：`"single_port"`, `"dual_port"`, `"true_dual_port"`, `"rom"` |
| `has_mask` | bool | 该接口是否支持写 mask |
| `async` | bool | 仅 dual_port：是否使用双时钟（异步） |
| `port_map` | object | 逻辑端口名 → vendor 物理端口名的映射 |
| `sub_types` | array | Vendor cell 家族的常量端口配置列表 |

#### port_map 极性编码

port_map 的 value 中，`~` 前缀表示**取反**：

```json
"port_map": {
    "clk":   "CLK",      // 直连
    "cen":   "~CEB",     // wrapper 侧 cen=1 表示使能，连接到 vendor 的 CEB 时取反
    "wen":   "~WEB",     // wrapper 侧 wen=1 表示写，连接到 vendor 的 WEB 时取反
    "addr":  "A",
    "wdata": "D",
    "rdata": "Q"
}
```

> **所有 wrapper 接口均为 active-HIGH 语义**。极性转换在 L1 physical_wrapper 内部自动处理。

#### port_map 逻辑端口名一览

根据 `base_type` 不同，使用不同的端口名集合：

**single_port (`1rw` / `1rwm`)**:

| 逻辑端口 | 说明 | 仅 mask 类型 |
|----------|------|-------------|
| `clk` | 时钟 | |
| `cen` | 片选使能 | |
| `wen` | 写使能 | |
| `addr` | 地址 | |
| `wdata` | 写数据 | |
| `rdata` | 读数据 | |
| `bwen` | 按位写使能 | 是 |

**dual_port — 同步 (`1r1w` / `1r1wm`)**:

| 逻辑端口 | 说明 | 仅 mask 类型 |
|----------|------|-------------|
| `clk` | 时钟（共享） | |
| `wr_en` | 写使能 | |
| `wr_addr` | 写地址 | |
| `wr_data` | 写数据 | |
| `rd_en` | 读使能 | |
| `rd_addr` | 读地址 | |
| `rd_data` | 读数据 | |
| `wr_mask` | 写 mask | 是 |

**dual_port — 异步 (`1r1wa` / `1r1wma`)**:

| 逻辑端口 | 说明 | 仅 mask 类型 |
|----------|------|-------------|
| `wr_clk` | 写时钟 | |
| `rd_clk` | 读时钟 | |
| `wr_en` | 写使能 | |
| `wr_addr` | 写地址 | |
| `wr_data` | 写数据 | |
| `rd_en` | 读使能 | |
| `rd_addr` | 读地址 | |
| `rd_data` | 读数据 | |
| `wr_mask` | 写 mask | 是 |

**true_dual_port (`2rw` / `2rwm`)**:

| 逻辑端口 | 说明 | 仅 mask 类型 |
|----------|------|-------------|
| `a_clk`, `b_clk` | A/B 端口时钟 | |
| `a_cen`, `b_cen` | A/B 片选 | |
| `a_wen`, `b_wen` | A/B 写使能 | |
| `a_addr`, `b_addr` | A/B 地址 | |
| `a_wdata`, `b_wdata` | A/B 写数据 | |
| `a_rdata`, `b_rdata` | A/B 读数据 | |
| `a_bwen`, `b_bwen` | A/B 写 mask | 是 |

**rom**:

| 逻辑端口 | 说明 |
|----------|------|
| `clk` | 时钟 |
| `cen` | 片选使能 |
| `addr` | 地址 |
| `rdata` | 读数据 |

#### sub_types 数组

每个 sub_type 条目定义一组 vendor cell 家族的常量端口配置：

```json
{
    "names": ["1prf", "uhd1prf", "spsbsram", "uhdspsram", "spmbsram", "hsspsram"],
    "const_ports": {
        "BWEB": "zeros",
        "TM_A": 0, "TM_CEB": 1, "TM_WEB": 1, "TM_D": 0, "TM_BWEB": 1,
        "BIST": 0, "SCAN_EN": 0, "SHUTDOWN": 0, "DEEP_SLEEP": 0, "DEEP_SLEEP_LV": 0,
        "DFT_BYPASS": 0, "FUSE_IO": 0, "REDUNDANCY_IO": 0,
        "SCAN_IN_C": 0, "SCAN_IN_D": 0,
        "RD_MARGIN": "{2'b10}", "WR_MARGIN": "{2'b01}"
    },
    "output_ports": ["SCAN_OUT_C", "SCAN_OUT_D", "PU_DLY_DS", "PU_DLY_SD"]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `names` | array[string] | 此组配置适用的 sub_type 名称列表。`physical.sub_type` 的值必须能在某组 `names` 中找到 |
| `const_ports` | object | 需要常量 tie-off 的 vendor 端口。值类型：`0`/`1` = 单 bit 常量；`"zeros"` = 全 0（位宽随数据位宽）；`"{2'b10}"` = Verilog 字面量 |
| `output_ports` | array[string] | vendor 未使用的输出端口名列表，wrapper 中不连接 |

> **non-mask 类型**（如 `1rw`）的 `const_ports` 中会包含 `"BWEB": "zeros"`，将 mask 端口 tie-off 为全写入。
> **mask 类型**（如 `1rwm`）的 `const_ports` 中**不包含** BWEB，因为 BWEB 已在 `port_map` 中映射为用户接口。

---

## 7. 类型体系

sram_mem_gen 支持 9 种 interface_type，命名规则：`<端口数><功能><修饰符>`

| 类型 | base_type | has_mask | async | 说明 |
|------|-----------|----------|-------|------|
| `1rw` | single_port | false | — | 单端口读写，无 mask |
| `1rwm` | single_port | true | — | 单端口读写，带 mask |
| `1r1w` | dual_port | false | false | 同步双端口（1 读 + 1 写），无 mask |
| `1r1wm` | dual_port | true | false | 同步双端口，带 mask |
| `1r1wa` | dual_port | false | true | 异步双端口（双时钟），无 mask |
| `1r1wma` | dual_port | true | true | 异步双端口，带 mask |
| `2rw` | true_dual_port | false | — | 真双端口（A/B 各可读写），无 mask |
| `2rwm` | true_dual_port | true | — | 真双端口，带 mask |
| `rom` | rom | false | — | 只读存储器 |

修饰符含义：
- `m` = 带 byte-write mask
- `a` = 异步（双时钟域）

> 详细接口定义和时序图参见 `doc/memory_types_spec.md`。

---

## 8. ECC 配置

### 8.1 工作原理

ECC 使用 SEC-DED（Single Error Correction, Double Error Detection）码。数据按固定大小分片（slice），每片独立编解码。

- **slice_count** = ceil(width / data_bits_per_slice)
- 物理存储宽度 = slice_count × (data_bits_per_slice + ecc_bits_per_slice)

### 8.2 编码类型

| code_type | 说明 |
|-----------|------|
| `"hamming"` | 标准 Hamming SECDED 码 |
| `"hsiao"` | Hsiao 修正 Hamming 码（优化 Fan-In） |

### 8.3 参数选择指南

常用配置：

| data_bits_per_slice (k) | ecc_bits_per_slice (m) | 总宽度 (n=k+m) | 适用场景 |
|------------------------|----------------------|----------------|---------|
| 8 | 5 | 13 | 窄位宽，高保护率 |
| 32 | 7 | 39 | 中等位宽 |
| 64 | 8 | 72 | 标准 ECC |
| 102 | 8 | 110 | 宽位宽，适配 110-bit cell |

> `m` 的最小值由 `k` 决定（SECDED 理论约束）。工具会在校验时自动检查并报错。

### 8.4 ECC + Mask 注意事项

当 ECC 和 coarse mask（`mask_gran > 1`）同时启用时：
- 如果 ECC codeword 宽度 `n` 不是 `mask_gran` 的整数倍，工具会自动将 codeword 填充（pad）到 `mask_gran` 的整数倍，确保 ECC slice 边界与物理 mask byte 边界对齐
- 填充会增加物理存储位宽，需确保 `lib_width` 足够

### 8.5 ECC 生成端口

启用 ECC 后，L2 wrapper 会增加以下端口：

| 端口 | 方向 | 位宽 | 说明 |
|------|------|------|------|
| `o_ecc_err_correctable` | output | 1 | 可纠正错误标志（单 bit 翻转） |
| `o_ecc_err_uncorrectable` | output | 1 | 不可纠正错误标志（双 bit 翻转） |
| `i_ecc_err_insert` | input | 2 | 错误注入控制（DFT 用途） |
| `i_ecc_err_mask` | input | 2 | 错误屏蔽控制 |

当 `detailed_report: true` 时，额外输出 ECC syndrome 信号。

---

## 9. 三层封装架构

```
L3: bypass_wrapper — 读写同地址 bypass
  └── L2: memory_wrapper — ECC + Init FSM + Pipeline
       └── L1: physical_wrapper — Tiling (col×row)
            └── N×M 块 Vendor Memory
```

### 9.1 L1: physical_wrapper

- 处理物理 cell 的列拼接（位宽不足时多列并排）和行拼接（深度不足时多行堆叠）
- 不含复位端口（`rst_n`）
- 位宽不整除时高位自动 padding（写入补 0，读出截断）
- 深度不整除时最后一行地址范围自动限制

### 9.2 L2: memory_wrapper

需 `enable_l2: true`（默认）。功能：

- **Input Pipeline**：可配置级数的输入寄存器
- **Init FSM**：上电后自动初始化所有地址（ROM 类型不生成）
- **ECC Encode/Decode**：数据编码后写入，读出后解码纠错
- **Mask 扩展**：ECC 校验位对应的 mask 强制为 1（确保 ECC 完整性）
- **Output Pipeline**：可配置级数的输出寄存器

### 9.3 L3: bypass_wrapper

需 `enable_l3: true` 且满足条件。功能：

- 当读写地址相同时，bypass 流水线延时，直接将写入数据转发到读出端口
- 仅支持同步双端口类型（`1r1w`, `1r1wm`）
- bypass_depth = input_pipe + ram_rd_latency + ecc_pipe + output_pipe

---

## 10. 输出产物

### 10.1 目录结构

```
output/
├── report.json              # 生成报告（JSON）
├── rtl/
│   ├── filelist.f            # 所有 RTL 文件列表
│   ├── common/
│   │   ├── data_syncn.v      # 同步器模块
│   │   └── std/              # 标准单元行为模型
│   │       └── *.v
│   ├── {top_name}_top.v      # 顶层模块（或 _phy, _mem, _top 分层）
│   ├── {prefix}_secded_*.sv  # ECC 编解码模块（如启用）
│   └── subsys_a/sram/        # 自定义 output_dir 子目录
│       └── *.v
├── tb/
│   ├── tb_{top_name}.v       # Testbench 文件
│   ├── *.hex                 # 激励数据
│   └── Makefile              # 仿真入口（所有实例）
└── sim/                      # 仿真运行产物（make 执行后生成）
```

### 10.2 filelist.f

自动生成的 RTL 文件列表，可直接传给 EDA 工具：

```bash
# VCS
vcs -f output/rtl/filelist.f

# Verilator
verilator --cc -f output/rtl/filelist.f

# Icarus Verilog
iverilog -f output/rtl/filelist.f -o sim.vvp
```

### 10.3 report.json

包含每个实例的生成结果：

```json
{
  "generated_at": "2026-03-27T00:36:33",
  "project": "my_soc",
  "prefix": "soc",
  "memories": [
    {
      "top_name": "soc_icache_RAM_1rw_128x256",
      "config_hash": "1db1aa28e9fa2902",
      "name": "icache",
      "physical": {
        "col_count": 2,
        "row_count": 1,
        "total_blocks": 2,
        "width_pad_bits": 0
      },
      "total_read_latency": {
        "input_pipe": 1,
        "ram_rd_latency": 1,
        "ecc_pipe": 0,
        "output_pipe": 1,
        "total": 3
      },
      "output_files": [...]
    }
  ]
}
```

---

## 11. 增量生成

默认启用增量生成模式。工具会对比每个实例的配置 hash（写入 `report.json`），仅重新生成配置有变化的实例。

- **增量模式**（默认）：跳过 hash 未变的实例，加速重复生成
- **全量模式**（`--full`）：忽略缓存，重新生成所有实例

> `filelist.f` 和 `output/tb/Makefile` 始终全量生成，无论增量/全量模式。

---

## 12. 仿真验证

### 12.1 运行单个仿真

```bash
make -C output/tb soc_icache_RAM_1rw_128x256
```

### 12.2 运行所有仿真

```bash
make -C output/tb sim
```

### 12.3 仿真流程

TB 的验证流程：
1. `$readmemh` 加载 hex 激励文件
2. 顺序写入所有地址
3. 顺序读出所有地址
4. 逐地址比对写入值和读出值
5. Mask 类型额外验证：masked 位保持旧值，非 masked 位正确写入

### 12.4 运行单元测试

```bash
# 设置 Python 路径
export PYTHONPATH=scripts:scripts/tests

# 运行所有测试
python3 -m pytest scripts/tests/ -v

# 运行特定模块的测试
python3 -m pytest scripts/tests/test_config_io.py -v
python3 -m pytest scripts/tests/test_physical_wrapper_gen.py -v
python3 -m pytest scripts/tests/test_memory_wrapper_gen.py -v
```

---

## 13. 版本管理

### 13.1 当前版本

项目当前版本 `0.1.0`，定义在 `pyproject.toml` 中。

### 13.2 配置版本控制建议

建议将以下文件纳入版本管理（git）：

| 文件/目录 | 说明 |
|-----------|------|
| `config/mem_config.json` | 用户配置（每项目一份） |
| `config/vendor_port_map.json` | Vendor 端口映射（每 vendor 一份） |
| `scripts/` | 生成器源码 |
| `doc/` | 文档 |
| `pyproject.toml` | 项目元数据与依赖 |

建议将以下文件**不纳入**版本管理：

| 文件/目录 | 说明 |
|-----------|------|
| `output/` | 生成产物（可重新生成） |
| `vendor/` | Vendor 提供的 cell 模型（受 NDA 保护） |
| `.venv/` | Python 虚拟环境 |

### 13.3 多项目管理

不同项目可使用不同的 `mem_config.json`，共享同一套 `vendor_port_map.json`：

```bash
# 项目 A
python3 scripts/mem_gen.py \
    --config-file projects/chip_a/mem_config.json \
    --output-dir projects/chip_a/output

# 项目 B
python3 scripts/mem_gen.py \
    --config-file projects/chip_b/mem_config.json \
    --output-dir projects/chip_b/output
```

---

## 14. 常见问题

### Q: 报错 "vendor cell 'xxx' not found in lib_paths"

确保 vendor cell 文件（`.v` 或 `.sv`）已放入 `vendor/` 目录，且文件名（不含扩展名）与 `physical.lib_name` 完全一致。

### Q: 报错 "Cannot infer sub_type from lib_name"

`lib_name` 的前缀在 `vendor_port_map.json` 的 `lib_name_map` 中无匹配。请检查 lib_name 拼写，或在 `physical` 中显式提供 `sub_type`。

### Q: 报错 "sub_type 'xxx' not found in interface_type"

该 `sub_type` 不适用于当前 `type`。例如 `sub_type="2prf"` 不能用于 `type="1rw"`。请查阅 `vendor_port_map.json` 中对应 interface_type 的 `sub_types[].names` 列表。

### Q: 报错 "lib_width must be evenly divisible by lib_mask_width"

mask 粒度必须整除。例如 `lib_width=32` 时，`lib_mask_width` 可选 1, 2, 4, 8, 16, 32。

### Q: 报错 "enable_l3=true is only supported for sync dual_port types"

L3 bypass_wrapper 仅支持 `1r1w` 和 `1r1wm` 类型。异步双端口、单端口、真双端口和 ROM 不支持。

### Q: ECC 报错 "ecc_bits_per_slice < minimum"

ECC 校验位数不足。请增大 `ecc_bits_per_slice` 或减小 `data_bits_per_slice`。工具会提示所需的最小值。

### Q: 如何在没有 vendor cell 文件的情况下验证生成？

可以使用 `vendor_model_gen.py` 生成行为仿真模型（这是辅助工具，不是核心生成流程的一部分）：

```bash
python3 demo/vendor_model_gen.py
```

生成的模型放在 `vendor/` 目录，可用于仿真验证。

### Q: Python 版本低于 3.11

本项目使用了 Python 3.11+ 的语法特性（如 `X | Y` 类型联合）。如果目标环境 Python 版本低于 3.11，需要升级 Python 或从源码编译安装。
