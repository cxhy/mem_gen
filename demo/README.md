# demo/ — 开发阶段仿真辅助工具

> **定位说明**：本目录的工具**不是** sram_mem_gen 核心 RTL 生成流程的一部分，
> 仅用于本项目开发阶段生成 vendor 行为仿真模型。

---

## 文件说明

| 文件 | 用途 |
|------|------|
| `vendor_model_gen.py` | 基于 `vendor_port_map.json` 自动生成 vendor SRAM 行为级仿真模型 |

---

## 核心 scripts/ vs demo/ 边界

| 目录 | 内容 | 是否生产可用 |
|------|------|-------------|
| `scripts/` | RTL 生成器、配置加载、TB 生成 | **是**，不依赖特定 vendor |
| `demo/` | vendor 行为模型生成、仿真辅助 | **否**，绑定本项目开发环境 |

真实用户拥有自己的 vendor SRAM 库（含 SPICE/Verilog 仿真模型），不需要使用本目录工具。

---

## vendor_model_gen.py 使用说明

该脚本根据 `config/vendor_port_map.json` 中的端口映射，生成行为级 Verilog 仿真模型，
输出到 `vendor/` 目录。

```bash
# 在项目根目录执行
uv run python demo/vendor_model_gen.py
```

生成的模型仅用于 Verilator/VCS 功能仿真，**不代表真实 vendor SRAM 的时序特性**。
