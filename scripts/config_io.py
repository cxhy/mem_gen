"""Configuration loading, dataclass definitions, report writing, and vendor lib checking."""

from __future__ import annotations

import hashlib
import json
import math
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import secded_gen


# ---------------------------------------------------------------------------
# Input config dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EccConfig:
    enable: bool
    code_type: str = ""
    data_bits_per_slice: int = 0
    ecc_bits_per_slice: int = 0
    seed: int | None = None
    detailed_report: bool = False


@dataclass(frozen=True)
class PhysicalConfig:
    sub_type: str
    lib_name: str
    lib_width: int
    lib_depth: int
    lib_mask_width: int = 0


@dataclass(frozen=True)
class MemorySpec:
    name: str
    type: str
    width: int
    depth: int
    ecc: EccConfig
    physical: PhysicalConfig
    ram_rd_latency: int
    input_pipe_stages: int
    ecc_pipe_stages: int
    output_pipe_stages: int
    enable_l2: bool = True
    enable_l3: bool = False
    output_dir: str = ""


@dataclass(frozen=True)
class SubTypeInfo:
    """A vendor const-port variant within an interface_type."""
    names: tuple[str, ...]
    const_ports: dict[str, object]   # PIN → int or str (Verilog literal)
    output_ports: tuple[str, ...]    # unconnected vendor output pins


@dataclass(frozen=True)
class InterfaceType:
    """Parsed interface_type from vendor_port_map.json."""
    base_type: str                # "single_port" | "dual_port" | "true_dual_port" | "rom"
    has_mask: bool
    is_async: bool                # dual_port only: true = two clocks
    port_map: dict[str, str]      # logical_name → "~?VENDOR_PIN"
    sub_types: tuple[SubTypeInfo, ...]


@dataclass(frozen=True)
class VendorPortMap:
    vendor: str
    lib_paths: tuple[str, ...]
    interface_types: dict[str, InterfaceType]
    lib_name_map: dict[str, str] = None          # prefix → sub_type
    lib_name_strip_suffixes: tuple[str, ...] = () # voltage suffixes to strip

    def __post_init__(self) -> None:
        if self.lib_name_map is None:
            object.__setattr__(self, "lib_name_map", {})


@dataclass(frozen=True)
class ProjectConfig:
    project: str
    prefix: str
    memories: tuple[MemorySpec, ...]
    vendor_port_map: VendorPortMap


# ---------------------------------------------------------------------------
# Calculation result dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EccParams:
    enabled: bool
    logical_total_width: int
    slice_count: int = 0
    data_pad_width: int = 0
    ecc_total_bits: int = 0
    data_with_ecc_width: int = 0
    pad_bits: int = 0
    k: int = 0
    m: int = 0
    n: int = 0


@dataclass(frozen=True)
class EccModuleInfo:
    enc_module: str
    dec_module: str
    seed_used: int


@dataclass(frozen=True)
class TilingParams:
    col_count: int
    row_count: int
    total_blocks: int
    width_pad_bits: int
    mask_pad_bits: int = 0
    total_mask_width: int = 0
    mask_gran: int = 1


# ---------------------------------------------------------------------------
# Port map polarity helpers
# ---------------------------------------------------------------------------

def parse_pin(port_map_value: str) -> tuple[str, bool]:
    """Parse a port_map value into (vendor_pin_name, is_inverted).

    Examples:
        "CLK"  → ("CLK", False)
        "~CEB" → ("CEB", True)
    """
    if port_map_value.startswith("~"):
        return port_map_value[1:], True
    return port_map_value, False


def pin_connect(port_map_value: str, signal_expr: str) -> str:
    """Generate Verilog connection expression respecting polarity.

    Examples:
        pin_connect("CLK", "clk")   → ".CLK (clk)"
        pin_connect("~CEB", "cen")  → ".CEB (~cen)"
    """
    pin_name, inverted = parse_pin(port_map_value)
    if inverted:
        return f".{pin_name} (~({signal_expr}))"
    return f".{pin_name} ({signal_expr})"


# ---------------------------------------------------------------------------
# Sub-type resolution
# ---------------------------------------------------------------------------

def resolve_sub_type(itype: InterfaceType, sub_type_name: str) -> SubTypeInfo:
    """Find the matching SubTypeInfo by name within an InterfaceType."""
    for st in itype.sub_types:
        if sub_type_name in st.names:
            return st
    valid_names = [n for st in itype.sub_types for n in st.names]
    raise ValueError(
        f"sub_type '{sub_type_name}' not found in interface_type "
        f"(available: {valid_names})"
    )


def resolve_sub_type_from_lib_name(
    lib_name: str,
    lib_name_map: dict[str, str],
    strip_suffixes: tuple[str, ...] | list[str] = (),
) -> str:
    """Infer sub_type from lib_name using prefix matching.

    Algorithm (matches legacy GetMemType):
      1. Lower-case the lib_name
      2. Strip voltage suffixes (ulvt, svt, lvt …)
      3. Match longest prefix in lib_name_map
    """
    name = lib_name.lower()
    for suffix in strip_suffixes:
        name = name.replace(suffix, "")

    best_prefix = ""
    best_sub_type = ""
    for prefix, sub_type in lib_name_map.items():
        if name.startswith(prefix) and len(prefix) > len(best_prefix):
            best_prefix = prefix
            best_sub_type = sub_type

    if not best_prefix:
        raise ValueError(
            f"Cannot infer sub_type from lib_name '{lib_name}': "
            f"no matching prefix in lib_name_map "
            f"(available prefixes: {list(lib_name_map.keys())})"
        )
    return best_sub_type


# ---------------------------------------------------------------------------
# Naming and hashing helpers
# ---------------------------------------------------------------------------

def build_top_name(prefix: str, name: str, mem_type: str,
                   width: int, depth: int) -> str:
    """Build the canonical top-level base name for a memory instance.

    Result: {prefix}[_{name}]_RAM_{type}_{width}x{depth}
    """
    base = f"{prefix}_{name}" if name else prefix
    return f"{base}_RAM_{mem_type}_{width}x{depth}"


def compute_config_hash(raw_dict: dict) -> str:
    """Compute a deterministic 16-char hex hash for a memory config dict."""
    config_str = json.dumps(raw_dict, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(config_str.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# ConfigLoader
# ---------------------------------------------------------------------------

VALID_BASE_TYPES = ("single_port", "dual_port", "true_dual_port", "rom")


class ConfigLoader:
    """Load and validate JSON configuration into frozen dataclasses."""

    def load(self, config_dir: Path, config_file: str | None = None) -> ProjectConfig:
        if config_file:
            mem_cfg_path = Path(config_file)
            if not mem_cfg_path.is_absolute():
                mem_cfg_path = Path.cwd() / mem_cfg_path
        else:
            mem_cfg_path = config_dir / "mem_config.json"

        vendor_cfg_path = config_dir / "vendor_port_map.json"

        with open(mem_cfg_path, "r", encoding="utf-8") as f:
            mem_cfg = json.load(f)
        with open(vendor_cfg_path, "r", encoding="utf-8") as f:
            vendor_cfg = json.load(f)

        assert "project" in mem_cfg, "Missing 'project' in config"
        assert "prefix" in mem_cfg, "Missing 'prefix' in config"
        assert "memories" in mem_cfg, "Missing 'memories' in config"
        assert "interface_types" in vendor_cfg, \
            "Missing 'interface_types' in vendor_port_map.json"

        vendor_port_map = self._parse_vendor_port_map(vendor_cfg)

        memories = []
        for mem in mem_cfg["memories"]:
            self._validate_memory(mem, vendor_port_map)
            memories.append(self._parse_memory(mem))

        return ProjectConfig(
            project=mem_cfg["project"],
            prefix=mem_cfg["prefix"],
            memories=tuple(memories),
            vendor_port_map=vendor_port_map,
        )

    def _parse_vendor_port_map(self, vendor_cfg: dict) -> VendorPortMap:
        vendor = vendor_cfg.get("vendor", "unknown")
        lib_paths = tuple(vendor_cfg.get("lib_paths", []))

        interface_types: dict[str, InterfaceType] = {}
        for type_name, type_def in vendor_cfg["interface_types"].items():
            base_type = type_def["base_type"]
            if base_type not in VALID_BASE_TYPES:
                raise ValueError(
                    f"interface_types.{type_name}: invalid base_type '{base_type}', "
                    f"must be one of: {VALID_BASE_TYPES}"
                )

            sub_types_raw = type_def.get("sub_types", [])
            sub_types = tuple(
                SubTypeInfo(
                    names=tuple(st["names"]),
                    const_ports=dict(st.get("const_ports", {})),
                    output_ports=tuple(st.get("output_ports", [])),
                )
                for st in sub_types_raw
            )

            interface_types[type_name] = InterfaceType(
                base_type=base_type,
                has_mask=type_def.get("has_mask", False),
                is_async=type_def.get("async", False),
                port_map=dict(type_def["port_map"]),
                sub_types=sub_types,
            )

        return VendorPortMap(
            vendor=vendor,
            lib_paths=lib_paths,
            interface_types=interface_types,
            lib_name_map=dict(vendor_cfg.get("lib_name_map", {})),
            lib_name_strip_suffixes=tuple(
                vendor_cfg.get("lib_name_strip_suffixes", [])
            ),
        )

    def _validate_memory(self, mem: dict, vendor_port_map: VendorPortMap) -> None:
        required = [
            "name", "type", "width", "depth", "ecc", "physical",
            "ram_rd_latency", "input_pipe_stages", "output_pipe_stages",
        ]
        for f in required:
            if f not in mem:
                raise ValueError(
                    f"Memory '{mem.get('name', '?')}': missing field '{f}'"
                )

        # Validate pipe stages and latency are non-negative integers
        for field in ("ram_rd_latency", "input_pipe_stages", "output_pipe_stages"):
            val = mem.get(field, 0)
            if not isinstance(val, int) or val < 0:
                raise ValueError(
                    f"Memory '{mem['name']}': '{field}' must be a non-negative integer, "
                    f"got {val!r}"
                )
        if "ecc_pipe_stages" in mem:
            val = mem["ecc_pipe_stages"]
            if not isinstance(val, int) or val < 0:
                raise ValueError(
                    f"Memory '{mem['name']}': 'ecc_pipe_stages' must be a non-negative "
                    f"integer, got {val!r}"
                )

        mem_type = mem["type"]
        if mem_type not in vendor_port_map.interface_types:
            raise ValueError(
                f"Memory '{mem['name']}': type '{mem_type}' not in vendor_port_map "
                f"(available: {list(vendor_port_map.interface_types.keys())})"
            )

        interface_type = vendor_port_map.interface_types[mem_type]

        # Validate ECC config
        ecc = mem["ecc"]
        if ecc.get("enable", False):
            ecc_required = [
                "code_type", "data_bits_per_slice",
                "ecc_bits_per_slice",
            ]
            for f in ecc_required:
                if f not in ecc:
                    raise ValueError(
                        f"Memory '{mem['name']}': ecc.enable=true but missing ecc.{f}"
                    )

            if "module_prefix" in ecc:
                warnings.warn(
                    f"Memory '{mem.get('name', '?')}': ecc.module_prefix is deprecated "
                    f"and will be ignored. Use top-level 'prefix' instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )

            k = ecc["data_bits_per_slice"]
            m = ecc["ecc_bits_per_slice"]
            min_m = secded_gen.min_paritysize(k)
            if m < min_m:
                raise ValueError(
                    f"Memory '{mem['name']}': ecc_bits_per_slice ({m}) < minimum ({min_m}) "
                    f"for data_bits_per_slice={k}"
                )

            code_type = ecc["code_type"]
            if code_type not in secded_gen.CODE_OPTIONS:
                raise ValueError(
                    f"Memory '{mem['name']}': invalid code_type '{code_type}', "
                    f"valid options: {list(secded_gen.CODE_OPTIONS.keys())}"
                )

        # Validate physical config
        phys = mem["physical"]
        for f in ["lib_name", "lib_width", "lib_depth"]:
            if f not in phys:
                raise ValueError(f"Memory '{mem['name']}': missing physical.{f}")

        # Resolve sub_type: explicit or inferred from lib_name
        if "sub_type" not in phys:
            if not vendor_port_map.lib_name_map:
                raise ValueError(
                    f"Memory '{mem['name']}': physical.sub_type is missing "
                    f"and no lib_name_map configured for auto-inference"
                )
            inferred = resolve_sub_type_from_lib_name(
                phys["lib_name"],
                vendor_port_map.lib_name_map,
                vendor_port_map.lib_name_strip_suffixes,
            )
            phys["sub_type"] = inferred

        if interface_type.has_mask:
            lib_mask_width = phys.get("lib_mask_width", 0)
            if lib_mask_width <= 0:
                raise ValueError(
                    f"Memory '{mem['name']}': type '{mem_type}' has_mask=true "
                    f"but physical.lib_mask_width is missing or <= 0"
                )
            lib_width = phys["lib_width"]
            if lib_width % lib_mask_width != 0:
                raise ValueError(
                    f"Memory '{mem['name']}': lib_width ({lib_width}) must be "
                    f"evenly divisible by lib_mask_width ({lib_mask_width})"
                )
            mask_gran = lib_width // lib_mask_width
            if mem["width"] % mask_gran != 0:
                raise ValueError(
                    f"Memory '{mem['name']}': width ({mem['width']}) must be "
                    f"evenly divisible by mask_gran "
                    f"({mask_gran} = lib_width {lib_width} / lib_mask_width {lib_mask_width})"
                )
            if ecc.get("enable", False) and mask_gran > 1:
                k = ecc["data_bits_per_slice"]
                slice_count = math.ceil(mem["width"] / k)
                if slice_count % lib_mask_width != 0:
                    raise ValueError(
                        f"Memory '{mem['name']}': ECC slice_count ({slice_count}) must be "
                        f"evenly divisible by lib_mask_width ({lib_mask_width})"
                    )

        # Validate sub_type exists in vendor_port_map
        sub_type_name = phys["sub_type"]
        try:
            resolve_sub_type(interface_type, sub_type_name)
        except ValueError:
            valid_names = [n for st in interface_type.sub_types for n in st.names]
            raise ValueError(
                f"Memory '{mem['name']}': physical.sub_type '{sub_type_name}' "
                f"not found in interface_type '{mem_type}' "
                f"(available: {valid_names})"
            )

        # Validate enable_l3 constraints
        enable_l3 = mem.get("enable_l3", False)
        if enable_l3:
            enable_l2 = mem.get("enable_l2", not mem.get("skip_l2", False))
            if not enable_l2:
                raise ValueError(
                    f"Memory '{mem['name']}': enable_l3=true requires enable_l2=true"
                )
            if interface_type.base_type != "dual_port" or interface_type.is_async:
                raise ValueError(
                    f"Memory '{mem['name']}': enable_l3=true is only supported for "
                    f"sync dual_port types (1r1w, 1r1wm), got '{mem_type}'"
                )

    def _parse_memory(self, mem: dict) -> MemorySpec:
        ecc_raw = mem["ecc"]
        ecc_config = EccConfig(
            enable=ecc_raw.get("enable", False),
            code_type=ecc_raw.get("code_type", ""),
            data_bits_per_slice=ecc_raw.get("data_bits_per_slice", 0),
            ecc_bits_per_slice=ecc_raw.get("ecc_bits_per_slice", 0),
            seed=ecc_raw.get("seed", None),
            detailed_report=ecc_raw.get("detailed_report", False),
        )

        phys_raw = mem["physical"]
        phys_config = PhysicalConfig(
            sub_type=phys_raw["sub_type"],
            lib_name=phys_raw["lib_name"],
            lib_width=phys_raw["lib_width"],
            lib_depth=phys_raw["lib_depth"],
            lib_mask_width=phys_raw.get("lib_mask_width", 0),
        )

        return MemorySpec(
            name=mem["name"],
            type=mem["type"],
            width=mem["width"],
            depth=mem["depth"],
            ecc=ecc_config,
            physical=phys_config,
            ram_rd_latency=mem["ram_rd_latency"],
            input_pipe_stages=mem["input_pipe_stages"],
            ecc_pipe_stages=mem.get("ecc_pipe_stages", 0),
            output_pipe_stages=mem["output_pipe_stages"],
            enable_l2=mem.get("enable_l2", not mem.get("skip_l2", False)),
            enable_l3=mem.get("enable_l3", False),
            output_dir=mem.get("output_dir", ""),
        )


# ---------------------------------------------------------------------------
# VendorLibChecker
# ---------------------------------------------------------------------------

class VendorLibChecker:
    """Verify vendor cell library files exist before generation."""

    @staticmethod
    def verify(project_config: ProjectConfig, project_root: Path | None = None) -> None:
        lib_paths = project_config.vendor_port_map.lib_paths
        if not lib_paths:
            print("  WARNING: No lib_paths configured, skipping vendor cell file verification")
            return

        for mem_spec in project_config.memories:
            lib_name = mem_spec.physical.lib_name
            found = False
            for search_dir in lib_paths:
                search_path = Path(search_dir)
                if not search_path.is_absolute() and project_root is not None:
                    search_path = project_root / search_path
                for ext in (".v", ".sv"):
                    candidate = search_path / f"{lib_name}{ext}"
                    if candidate.exists():
                        found = True
                        break
                if found:
                    break

            if not found:
                raise FileNotFoundError(
                    f"Memory '{mem_spec.name}': vendor cell '{lib_name}' not found "
                    f"in lib_paths: {list(lib_paths)}. "
                    f"Searched for {lib_name}.v / {lib_name}.sv"
                )


# ---------------------------------------------------------------------------
# ReportWriter
# ---------------------------------------------------------------------------

class ReportWriter:
    """Generate report.json with generation results."""

    def write(self, project: str, prefix: str,
              results: list[dict], outdir: Path) -> None:
        report = {
            "generated_at": datetime.now().isoformat(),
            "project": project,
            "prefix": prefix,
            "memories": results,
        }
        report_path = outdir / "report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"  Report written to {report_path}")
