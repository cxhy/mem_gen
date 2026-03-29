# sram_mem_gen 快速上手指南

本文档帮助你在 30 分钟内完成从配置到仿真的完整流程。

---

## 目录

1. [前置要求](#前置要求)
2. [Step 1 — 配置 vendor_port_map.json](#step-1--配置-vendor_port_mapjson)
3. [Step 2 — 配置 mem_config.json](#step-2--配置-mem_configjson)
4. [Step 3 — 生成与仿真验证](#step-3--生成与仿真验证)
5. [常见问题](#常见问题)

---

## 前置要求

| 工具 | 版本要求 | 说明 |
|------|----------|------|
| Python | 3.11+ | 通过 `uv` 管理，见下方安装步骤 |
| Verilator | 5.x | 仿真引擎，需要 VERILATOR_ROOT 环境变量 |
| uv | 最新版 | Python 包管理器 |

**安装依赖：**

```bash
# 克隆仓库后，在项目根目录执行
uv sync
```

---

## Step 1 — 配置 vendor_port_map.json

### 作用

`vendor_port_map.json` 告诉生成器：你的 vendor SRAM cell 的端口名称是什么，
以及如何将工具内部的统一接口（`clk`, `cen`, `addr` 等）映射到这些 vendor 端口。

### 文件位置

```
config/
├── vendor_port_map.json   ← 你需要编辑这个
└── mem_config.json
```

如果使用 demo 配置快速体验，可以直接跳过本节，使用已有的：

```
config/demo/vendor_port_map.json   ← demo 用通用信号名，可直接运行仿真
```

### 文件结构说明

```json
{
  "vendor": "your_vendor_name",
  "lib_paths": ["vendor"],          // vendor 行为模型的搜索路径

  // lib_name 前缀 → sub_type 名称的映射（用于自动推导）
  "lib_name_map": {
    "sp":  "sp_variant_a",          // 前缀 "sp" 对应的 sub_type
    "dp":  "dp_variant_a"
  },
  "lib_name_strip_suffixes": ["ulvt", "svt", "lvt"],  // 从 lib_name 末尾剥离的电压后缀

  "interface_types": {
    "1rw": { ... },   // 单端口读写
    "1r1w": { ... },  // 双端口读写
    "2rw": { ... },   // 真双端口
    "rom": { ... }    // 只读
    // ... 共 9 种类型，按需填写
  }
}
```

### 核心字段：port_map

`port_map` 定义内部端口名 → vendor 端口名的映射关系：

```json
"port_map": {
  "clk":   "CLK",      // 时钟
  "cen":   "~CEB",     // chip enable（~ 前缀 = 取反，vendor 低有效 → wrapper 高有效）
  "wen":   "~WEB",     // write enable（同上）
  "addr":  "A",        // 地址
  "wdata": "D",        // 写数据
  "rdata": "Q"         // 读数据（输出端口）
}
```

**`~` 前缀的含义：**

工具内部统一使用 active-HIGH 极性。如果你的 vendor cell 某端口为低有效（如 `CEB`，低有效片选），
则在 port_map 中写 `"~CEB"`，生成器会在 physical_wrapper 内部自动插入取反逻辑。

**各 interface_type 的 port_map 必填字段：**

| interface_type | 必填字段 |
|----------------|----------|
| `1rw` | clk, cen, wen, addr, wdata, rdata |
| `1rwm` | 同上 + bwen（bit write enable） |
| `1r1w` | clk, wr_en, wr_addr, wr_data, rd_en, rd_addr, rd_data |
| `1r1wm` | 同上 + wr_mask |
| `1r1wa` | wr_clk, rd_clk, wr_en, wr_addr, wr_data, rd_en, rd_addr, rd_data |
| `1r1wma` | 同上 + wr_mask |
| `2rw` | a_clk/b_clk, a_cen/b_cen, a_wen/b_wen, a_addr/b_addr, a_wdata/b_wdata, a_rdata/b_rdata |
| `2rwm` | 同上 + a_bwen, b_bwen |
| `rom` | clk, cen, addr, rdata |

### sub_types — vendor cell 变体

同一 interface_type 可能对应多种 vendor cell 变体（如标准速/高速/超低压），通过 `sub_types` 区分：

```json
"sub_types": [
  {
    "names": ["sp_generic"],   // 该变体的 sub_type 名称列表（用于 mem_config.json 中 physical.sub_type 字段）
    "const_ports": {           // 固定值端口（DFT/测试端口，在 wrapper 中 tie-off）
      "BWEN": "zeros",         // "zeros" = 全 0，宽度自动推导
      "TEST_EN": 0,            // 标量 0 或 1
      "SLEEP": 0
    },
    "output_ports": []         // vendor cell 的输出端口（wrapper 中悬空，无需连接）
  }
]
```

**const_ports 特殊值：**

| 值 | 含义 |
|----|------|
| `0` / `1` | 标量 tie-off |
| `"zeros"` | 向量全 0，宽度自动适配（通常用于 BWEN/mask） |
| `"{2'b01}"` | 原样展开为 Verilog 字面量 |

### 最小可用示例（1rw 类型）

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
        "clk":   "CK",
        "cen":   "~CSN",
        "wen":   "~WEN",
        "addr":  "A",
        "wdata": "D",
        "rdata": "Q"
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

---

## Step 2 — 配置 mem_config.json

### 作用

`mem_config.json` 描述项目中需要生成哪些 SRAM 实例，以及每个实例的规格。

### 文件结构

```json
{
  "project": "my_chip",            // 项目名（仅用于注释/报告）
  "prefix": "mc",                  // 生成 RTL 模块名的前缀，如 mc_RAM_1rw_32x256_top

  "memories": [
    { /* 实例 1 */ },
    { /* 实例 2 */ }
  ]
}
```

### 实例字段详解

```json
{
  "name": "icache_data",           // 实例名（生成的模块名包含此字段）
  "type": "1rw",                   // interface_type，必须在 vendor_port_map.json 中有定义

  // 目标规格（工具会自动做 tiling）
  "width": 64,                     // 数据位宽（bit）
  "depth": 1024,                   // 存储深度（word 数）

  // 读延迟与流水线配置
  "ram_rd_latency": 1,             // vendor cell 的固有读延迟（通常为 1 或 2）
  "input_pipe_stages": 0,          // 输入寄存器级数（0 = 直通）
  "ecc_pipe_stages": 0,            // ECC 流水线级数（仅在 enable=true 时生效）
  "output_pipe_stages": 0,         // 输出寄存器级数

  // ECC 配置
  "ecc": {
    "enable": false                // true = 启用 SECDED Hamming ECC
    // 启用时可选字段：
    // "code_type": "hamming",
    // "data_bits_per_slice": 64,
    // "ecc_bits_per_slice": 8
  },

  // vendor cell 物理规格
  "physical": {
    "sub_type": "sp_std",          // 对应 vendor_port_map.json 中 sub_types[].names
                                   // 可省略，工具会从 lib_name 自动推导
    "lib_name": "SRAM_SP_256x32",  // vendor cell 的库名（用于自动推导 sub_type）
    "lib_width": 32,               // vendor cell 的数据位宽
    "lib_depth": 256               // vendor cell 的深度
  },

  "enable_l2": true,               // true = 生成 L2 memory_wrapper（含 ECC/Init FSM/Pipeline）
  "enable_l3": false               // true = 生成 L3 bypass_wrapper（仅支持 1r1w/1r1wm）
}
```

### 字段速查

| 字段 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| name | 是 | — | 实例名，影响模块名 |
| type | 是 | — | interface_type（9 种之一） |
| width | 是 | — | 目标数据位宽 |
| depth | 是 | — | 目标深度 |
| ram_rd_latency | 否 | 1 | vendor cell 固有读延迟 |
| input_pipe_stages | 否 | 0 | 输入流水线级数 |
| ecc_pipe_stages | 否 | 0 | ECC 流水线级数 |
| output_pipe_stages | 否 | 0 | 输出流水线级数 |
| ecc.enable | 否 | false | 是否启用 ECC |
| physical.sub_type | 否 | 自动推导 | vendor cell 变体名 |
| physical.lib_name | 是 | — | vendor cell 库名 |
| physical.lib_width | 是 | — | vendor cell 位宽 |
| physical.lib_depth | 是 | — | vendor cell 深度 |
| enable_l2 | 否 | true | 生成 L2 wrapper |
| enable_l3 | 否 | false | 生成 L3 bypass wrapper |

### 带 ECC 的实例示例

```json
{
  "name": "ecc_buffer",
  "type": "1rw",
  "width": 64,
  "depth": 512,
  "ram_rd_latency": 1,
  "input_pipe_stages": 1,
  "ecc_pipe_stages": 0,
  "output_pipe_stages": 1,
  "ecc": {
    "enable": true,
    "code_type": "hamming",
    "data_bits_per_slice": 64,
    "ecc_bits_per_slice": 8
  },
  "physical": {
    "sub_type": "sp_std",
    "lib_name": "SRAM_SP_256x40",
    "lib_width": 40,
    "lib_depth": 256
  },
  "enable_l2": true
}
```

---

## Step 3 — 生成与仿真验证

### 3.1 使用 Demo 配置快速体验

`config/demo/` 提供两套配置，使用通用信号名，搭配自动生成的行为模型即可运行仿真：

| 配置文件 | 实例数 | 覆盖场景 |
|----------|--------|----------|
| `mem_config.json` | 3 | 基础场景（单端口/双端口带mask/ROM）|
| `comprehensive_mem_config.json` | 24 | 全场景（L1/L2/L3-bypass/ECC/padding/mask/tiling/async/TDP/ROM）|

```bash
# === 基础 Demo（3 实例）===

# Step 1：生成 vendor 行为仿真模型
uv run python demo/vendor_model_gen.py \
  --config-dir config/demo \
  --vendor-dir vendor/demo

# Step 2：运行生成器
uv run python scripts/mem_gen.py \
  --config-dir config/demo \
  --output-dir output/demo

# === 综合 Demo（24 实例，覆盖全部特性）===

# Step 1：生成 vendor 模型（--config-file 指向 comprehensive 配置）
uv run python demo/vendor_model_gen.py \
  --config-dir config/demo \
  --config-file config/demo/comprehensive_mem_config.json \
  --vendor-dir vendor/demo

# Step 2：运行生成器
uv run python scripts/mem_gen.py \
  --config-dir config/demo \
  --config-file config/demo/comprehensive_mem_config.json \
  --output-dir output/demo_comprehensive
```

### 3.2 使用自定义配置

```bash
# 生成（使用 config/ 目录下的配置文件）
uv run python scripts/mem_gen.py

# 指定自定义配置文件
uv run python scripts/mem_gen.py --config-file config/my_config.json

# 强制完整重新生成（忽略增量缓存）
uv run python scripts/mem_gen.py --full

# 跳过 testbench 生成（仅生成 RTL）
uv run python scripts/mem_gen.py --no-tb
```

### 3.3 运行仿真

Makefile 生成在 `output/<name>/tb/Makefile`，仿真产物输出到 `output/<name>/sim/`：

```bash
# 运行所有实例
make -C output/demo/tb

# 运行单个实例（目标名 = 完整模块名）
make -C output/demo/tb demo_sp_simple_RAM_1rw_32x256

# 综合 demo
make -C output/demo_comprehensive/tb
make -C output/demo_comprehensive/tb demo_sp_ecc_1s_RAM_1rw_64x256
```

### 3.4 判断生成是否成功

**检查 report.json：**

```bash
# 查看所有实例的生成状态
python -c "
import json
with open('output/demo/report.json') as f:
    r = json.load(f)
for m in r['memories']:
    status = 'OK' if m.get('generated') else 'SKIP'
    print(f\"{status}  {m['name']}  ({m['type']} {m['width']}x{m['depth']})\")
"
```

**检查 filelist.f：**

```bash
# 查看所有生成的 RTL 文件列表
cat output/demo/rtl/filelist.f
```

**仿真成功标志：**

仿真输出末尾出现 `PASS` 即为验证通过：

```
[SRAM_TB] All 256 vectors PASS
$finish
```

出现 `FAIL` 时，会打印具体地址、期望值和实际值。

---

## 常见问题

### Q: 生成时报 `KeyError: 'sub_type'`

`physical.sub_type` 的值在 `vendor_port_map.json` 对应 interface_type 的 `sub_types[].names` 中找不到。
检查两个文件中 sub_type 名称是否拼写一致。

### Q: 如何从 lib_name 自动推导 sub_type？

在 `vendor_port_map.json` 的顶层 `lib_name_map` 中配置前缀映射，例如：

```json
"lib_name_map": { "SRAM_SP": "sp_std" }
```

工具会将 `lib_name` 转小写、剥离电压后缀后，进行最长前缀匹配。

### Q: `enable_l2: false` 和 `true` 的区别？

- `false`：只生成 L1 physical_wrapper（模块名后缀 `_top`），无 ECC/流水线
- `true`：生成 L1（`_phy`）+ L2 memory_wrapper（`_top`），支持 ECC/Init FSM/流水线

### Q: 仿真报 `Cannot find module`

运行 `cat output/demo/rtl/filelist.f` 检查是否包含所有依赖文件。
确认 `vendor/` 目录下有对应的行为模型（由 `scripts/vendor_model_gen.py` 生成）。

### Q: ROM 类型如何初始化？

ROM 使用 `$readmemh` 加载初始化文件。生成器会自动生成 `_rom_init.hex`，
testbench 会在仿真开始时加载。实际综合时需替换为 foundry 提供的 ROM 内容生成工具。

---

## 输出目录结构

```
output/
├── report.json          # 生成报告（每次运行覆盖）
├── rtl/
│   ├── filelist.f       # RTL 文件列表（供 EDA 工具使用）
│   ├── common/std/      # 公共 std cell（std_dffe.v 等）
│   ├── lx_secded_*.sv   # ECC 编解码模块（仅在 ECC 启用时生成）
│   ├── *_physical_wrapper.v   # L1：tiling + vendor cell 例化
│   ├── *_memory_wrapper.v     # L2：ECC + Init FSM + 流水线
│   └── *_top.v                # 顶层模块（L1/L2/L3 之一）
├── tb/
│   ├── tb_*.v           # Testbench Verilog
│   └── *.hex            # 仿真激励数据
└── sim/
    ├── run_all.sh        # 运行所有实例仿真
    └── run_<name>.sh     # 单实例仿真脚本
```

> `output/` 已通过 `.gitignore` 排除，不纳入版本管理。

---

## 下一步

- 阅读 `doc/memory_types_spec.md` 了解 9 种 interface_type 的完整接口规格和时序
- 阅读 `doc/architecture.md` 了解三层封装架构设计
- 参考 `config/comprehensive_test_config.json` 查看覆盖所有类型的完整测试配置
