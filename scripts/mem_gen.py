#!/usr/bin/env python3
"""SRAM Memory Wrapper Generator — CLI entry point.

Generates Layer 1 (physical_wrapper) and Layer 2 (memory_wrapper) Verilog files
from JSON configuration. Integrates secded_gen for ECC encode/decode module generation.

Supports incremental generation (default) and full regeneration (--full).
"""

import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

# Ensure scripts/ is on sys.path for sibling module imports
SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from config_io import (
    ConfigLoader, EccParams, MemorySpec, ProjectConfig,
    ReportWriter, VendorLibChecker,
    build_top_name, compute_config_hash,
)
from bypass_wrapper_gen import gen_bypass_wrapper
from ecc_calculator import EccCalculator
from memory_wrapper_gen import gen_memory_wrapper
from physical_wrapper_gen import PhysicalWrapperGenerator, gen_physical_wrapper


def main():
    parser = argparse.ArgumentParser(description="SRAM Memory Wrapper Generator")
    parser.add_argument(
        "--config-dir",
        default=str(PROJECT_ROOT / "config"),
        help="Directory containing vendor_port_map.json (and mem_config.json if --config-file not set)",
    )
    parser.add_argument(
        "--config-file",
        default=None,
        help="Specific mem_config JSON file (overrides default mem_config.json)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "output"),
        help="Output directory",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Full regeneration (ignore incremental cache)",
    )
    parser.add_argument(
        "--no-tb",
        action="store_true",
        help="Skip testbench generation",
    )
    args = parser.parse_args()

    config_dir = Path(args.config_dir)
    output_dir = Path(args.output_dir).resolve()
    rtl_outdir = output_dir / "rtl"
    rtl_outdir.mkdir(parents=True, exist_ok=True)
    tb_outdir = output_dir / "tb"
    sim_outdir = output_dir / "sim"

    # Load configuration
    print("Loading configuration...")
    config_loader = ConfigLoader()
    project_config = config_loader.load(config_dir, args.config_file)
    prefix = project_config.prefix
    print(f"Project: {project_config.project}, prefix: {prefix}, "
          f"{len(project_config.memories)} memories")

    # Load raw JSON for config hash computation
    raw_memories = _load_raw_memories(config_dir, args.config_file)

    # Verify vendor cell files exist
    VendorLibChecker.verify(project_config, PROJECT_ROOT)

    # Copy shared files to common/
    common_dir = rtl_outdir / "common"
    _copy_data_syncn(common_dir)
    _copy_std_cells(common_dir)

    # Load old report for incremental comparison
    old_hash_map = _load_old_report(output_dir) if not args.full else {}
    if args.full:
        print("  Full regeneration mode (--full)")

    # Process each memory
    ecc_calculator = EccCalculator()
    report_writer = ReportWriter()

    results: list[dict] = []
    skip_count = 0
    gen_count = 0
    all_tb_top_names: list[str] = []  # all instances (for Makefile)

    for i, mem_spec in enumerate(project_config.memories):
        raw_mem = raw_memories[i]
        top_name = build_top_name(
            prefix, mem_spec.name, mem_spec.type,
            mem_spec.width, mem_spec.depth,
        )
        config_hash = compute_config_hash(raw_mem)
        all_tb_top_names.append(top_name)

        # Incremental check
        if not args.full and _can_skip(top_name, config_hash, old_hash_map):
            print(f"\n--- Skipping {top_name} (unchanged) ---")
            skip_count += 1
            # Carry forward old result
            old_result = _find_old_result(top_name, output_dir)
            if old_result:
                results.append(old_result)
            continue

        result = _process_memory(
            mem_spec, project_config, ecc_calculator,
            rtl_outdir, prefix, top_name, config_hash,
        )
        results.append(result)
        gen_count += 1

        # TB generation
        if not args.no_tb:
            _generate_tb(
                mem_spec, ecc_calculator, project_config,
                top_name, tb_outdir, sim_outdir,
            )

    # Generate filelist.f (always full, even in incremental mode)
    _write_filelist(results, project_config, rtl_outdir, PROJECT_ROOT)

    # Generate Makefile covering all instances (always, even in incremental mode)
    if not args.no_tb:
        from tb_gen import gen_makefile
        gen_makefile(all_tb_top_names, tb_outdir)
        print(f"  Makefile written to {tb_outdir}")

    report_writer.write(project_config.project, prefix, results, output_dir)
    print(f"\nDone! Generated: {gen_count}, Skipped: {skip_count}")


# ---------------------------------------------------------------------------
# Raw config loading (for hash computation)
# ---------------------------------------------------------------------------

def _load_raw_memories(config_dir: Path,
                       config_file: str | None) -> list[dict]:
    """Load raw memory dicts from JSON for config hash computation."""
    if config_file:
        mem_cfg_path = Path(config_file)
        if not mem_cfg_path.is_absolute():
            mem_cfg_path = Path.cwd() / mem_cfg_path
    else:
        mem_cfg_path = config_dir / "mem_config.json"

    with open(mem_cfg_path, "r", encoding="utf-8") as f:
        mem_cfg = json.load(f)

    return list(mem_cfg["memories"])


# ---------------------------------------------------------------------------
# Incremental generation helpers
# ---------------------------------------------------------------------------

def _load_old_report(output_dir: Path) -> dict[str, str]:
    """Load old report.json and return {top_name: config_hash} map."""
    report_path = output_dir / "report.json"
    if not report_path.exists():
        return {}
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        return {
            mem["top_name"]: mem["config_hash"]
            for mem in report.get("memories", [])
            if "top_name" in mem and "config_hash" in mem
        }
    except (json.JSONDecodeError, KeyError):
        return {}


def _can_skip(top_name: str, config_hash: str,
              old_hash_map: dict[str, str]) -> bool:
    """Check if a memory instance can be skipped (unchanged config)."""
    return old_hash_map.get(top_name) == config_hash


def _find_old_result(top_name: str, output_dir: Path) -> dict | None:
    """Find and return old report entry for a given top_name."""
    report_path = output_dir / "report.json"
    if not report_path.exists():
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        for mem in report.get("memories", []):
            if mem.get("top_name") == top_name:
                return mem
    except (json.JSONDecodeError, KeyError):
        pass
    return None


# ---------------------------------------------------------------------------
# Shared file copy helpers
# ---------------------------------------------------------------------------

def _copy_data_syncn(common_dir: Path) -> None:
    common_dir.mkdir(parents=True, exist_ok=True)
    src = SCRIPTS_DIR / "std" / "data_syncn.v"
    dst = common_dir / "data_syncn.v"
    if src.exists():
        shutil.copy2(src, dst)
        print(f"  Copied data_syncn.v to {dst}")
    else:
        print(f"  WARNING: {src} not found, skipping data_syncn.v copy")


def _copy_std_cells(common_dir: Path) -> None:
    """Copy behavioral std cell models to common/std/."""
    std_src = SCRIPTS_DIR / "std"
    std_dst = common_dir / "std"
    std_dst.mkdir(parents=True, exist_ok=True)
    for src_file in sorted(std_src.glob("*.v")):
        dst_file = std_dst / src_file.name
        shutil.copy2(src_file, dst_file)
    print(f"  Std cells copied to {std_dst}")


# ---------------------------------------------------------------------------
# Filelist generation
# ---------------------------------------------------------------------------

def _collect_common_files(rtl_outdir: Path) -> list[str]:
    """Collect common/ files relative to rtl_outdir."""
    common_dir = rtl_outdir / "common"
    entries: list[str] = []
    data_syncn = common_dir / "data_syncn.v"
    if data_syncn.exists():
        entries.append("common/data_syncn.v")
    std_dir = common_dir / "std"
    if std_dir.is_dir():
        for f in sorted(std_dir.glob("*.v")):
            entries.append(f"common/std/{f.name}")
    return entries


def _resolve_vendor_path(
    lib_name: str, lib_paths: tuple[str, ...], project_root: Path,
    rtl_outdir: Path,
) -> str | None:
    """Resolve a vendor cell file to a path relative to rtl_outdir."""
    for search_dir in lib_paths:
        search_path = Path(search_dir)
        if not search_path.is_absolute():
            search_path = project_root / search_path
        for ext in (".v", ".sv"):
            candidate = search_path / f"{lib_name}{ext}"
            if candidate.exists():
                rel = os.path.relpath(
                    candidate.resolve(), rtl_outdir.resolve(),
                )
                return rel.replace("\\", "/")
    return None


def _write_filelist(
    results: list[dict], project_config: ProjectConfig,
    rtl_outdir: Path, project_root: Path,
) -> None:
    """Write output/rtl/filelist.f with all generated and vendor files."""
    lines: list[str] = ["// Generated by sram_mem_gen"]
    seen_paths: set[str] = set()
    seen_basenames: set[str] = set()

    def _add(path: str, collection: list[str]) -> None:
        basename = path.rsplit("/", 1)[-1]
        if path not in seen_paths and basename not in seen_basenames:
            seen_paths.add(path)
            seen_basenames.add(basename)
            collection.append(path)

    # Common files
    common_entries: list[str] = []
    for p in _collect_common_files(rtl_outdir):
        _add(p, common_entries)
    if common_entries:
        lines.append("")
        lines.append("// Common files")
        lines.extend(common_entries)

    # Vendor models
    lib_paths = project_config.vendor_port_map.lib_paths
    vendor_entries: list[str] = []
    for mem_spec in project_config.memories:
        vp = _resolve_vendor_path(
            mem_spec.physical.lib_name, lib_paths, project_root, rtl_outdir,
        )
        if vp is not None:
            _add(vp, vendor_entries)
    if vendor_entries:
        lines.append("")
        lines.append("// Vendor models")
        lines.extend(vendor_entries)

    # Per-instance RTL files
    for result in results:
        top_name = result["top_name"]
        instance_entries: list[str] = []
        for of in result.get("output_files", []):
            abs_path = (project_root / of).resolve()
            rel = os.path.relpath(
                abs_path, rtl_outdir.resolve(),
            ).replace("\\", "/")
            _add(rel, instance_entries)
        if instance_entries:
            lines.append("")
            lines.append(f"// Instance: {top_name}")
            lines.extend(instance_entries)

    lines.append("")  # trailing newline
    filelist_path = rtl_outdir / "filelist.f"
    with open(filelist_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  Filelist written to {filelist_path}")


# ---------------------------------------------------------------------------
# Module name computation
# ---------------------------------------------------------------------------

def _compute_module_names(top_name: str, enable_l2: bool,
                          enable_l3: bool = False) -> dict[str, str]:
    """Compute module names for each layer.

    Returns dict with keys: l1_module, l2_module, l3_module.
    Values are None when the corresponding layer is disabled.

    Naming:
      l1 only          → L1=_top
      l1+l2            → L1=_phy, L2=_top
      l1+l2+l3         → L1=_phy, L2=_mem, L3=_top
    """
    if not enable_l2:
        return {
            "l1_module": f"{top_name}_top",
            "l2_module": None,
            "l3_module": None,
        }
    if not enable_l3:
        return {
            "l1_module": f"{top_name}_phy",
            "l2_module": f"{top_name}_top",
            "l3_module": None,
        }
    return {
        "l1_module": f"{top_name}_phy",
        "l2_module": f"{top_name}_mem",
        "l3_module": f"{top_name}_top",
    }


# ---------------------------------------------------------------------------
# TB generation helper
# ---------------------------------------------------------------------------

def _generate_tb(mem_spec: MemorySpec, ecc_calculator: EccCalculator,
                 project_config: ProjectConfig,
                 top_name: str, tb_outdir: Path, sim_outdir: Path) -> None:
    """Generate TB, hex stimulus, and sim script for one memory instance."""
    from tb_gen import gen_tb

    interface_type = project_config.vendor_port_map.interface_types[mem_spec.type]
    ecc_params = ecc_calculator.calc_params(mem_spec.width, mem_spec.ecc)

    names = _compute_module_names(top_name, mem_spec.enable_l2, mem_spec.enable_l3)
    phy_name = names["l1_module"]

    gen_tb(mem_spec, ecc_params, interface_type,
           top_name, phy_name, tb_outdir, sim_outdir)
    print(f"  TB written: {tb_outdir / f'tb_{top_name}.v'}")


# ---------------------------------------------------------------------------
# Per-memory processing
# ---------------------------------------------------------------------------

def _process_memory(mem_spec: MemorySpec, project_config: ProjectConfig,
                    ecc_calculator: EccCalculator,
                    rtl_outdir: Path, prefix: str,
                    top_name: str, config_hash: str) -> dict:
    print(f"\n--- Processing memory: {top_name} ---")

    # Compute output directory for this instance
    if mem_spec.output_dir:
        instance_outdir = rtl_outdir / mem_spec.output_dir
    else:
        instance_outdir = rtl_outdir
    instance_outdir.mkdir(parents=True, exist_ok=True)

    # Compute module names
    names = _compute_module_names(top_name, mem_spec.enable_l2, mem_spec.enable_l3)
    l1_module = names["l1_module"]
    l2_module = names["l2_module"]
    l3_module = names["l3_module"]

    # Lookup interface type
    interface_type = project_config.vendor_port_map.interface_types[mem_spec.type]

    # ECC calculation
    ecc_params = ecc_calculator.calc_params(mem_spec.width, mem_spec.ecc)
    logical_width = ecc_params.logical_total_width

    # Pad ECC codeword to mask_gran boundary when ECC + coarse mask
    if ecc_params.enabled and interface_type.has_mask and mem_spec.physical.lib_mask_width > 0:
        mask_gran = mem_spec.physical.lib_width // mem_spec.physical.lib_mask_width
        if mask_gran > 1:
            padded_n = math.ceil(ecc_params.n / mask_gran) * mask_gran
            if padded_n != ecc_params.n:
                logical_width = padded_n * ecc_params.slice_count
                from dataclasses import replace
                ecc_params = replace(ecc_params,
                                     logical_total_width=logical_width)

    # Tiling calculation
    tiling = PhysicalWrapperGenerator.calc_tiling(
        logical_width, mem_spec.depth,
        mem_spec.physical.lib_width, mem_spec.physical.lib_depth,
        mem_spec.physical.lib_mask_width,
    )

    # ECC module generation (output to instance dir)
    ecc_modules = None
    ecc_report = None
    if ecc_params.enabled:
        ecc_modules = ecc_calculator.generate_modules(
            mem_spec.ecc, prefix, str(instance_outdir),
        )
        ecc_report = {
            "slice_count": ecc_params.slice_count,
            "data_pad_width": ecc_params.data_pad_width,
            "data_with_ecc_width": ecc_params.data_with_ecc_width,
            "pad_bits": ecc_params.pad_bits,
            "seed_used": ecc_modules.seed_used,
            "enc_module": ecc_modules.enc_module,
            "dec_module": ecc_modules.dec_module,
        }
        print(f"  ECC: {ecc_params.slice_count} slices, "
              f"pad={ecc_params.pad_bits}, "
              f"total_w={ecc_params.data_with_ecc_width}")

    print(f"  Physical: {tiling.col_count}col x {tiling.row_count}row = "
          f"{tiling.total_blocks} blocks, width_pad={tiling.width_pad_bits}")

    # Layer 1: physical_wrapper
    phy_v = gen_physical_wrapper(
        mem_spec, ecc_params, tiling, interface_type, l1_module,
    )
    phy_path = instance_outdir / f"{l1_module}.v"
    with open(phy_path, "w", encoding="utf-8") as f:
        f.write(phy_v)
    print(f"  Written: {phy_path}")

    # Layer 2: memory_wrapper
    mem_path = None
    if mem_spec.enable_l2:
        mem_v = gen_memory_wrapper(
            mem_spec, ecc_params, ecc_modules, interface_type,
            l2_module, l1_module, tiling,
        )
        mem_path = instance_outdir / f"{l2_module}.v"
        with open(mem_path, "w", encoding="utf-8") as f:
            f.write(mem_v)
        print(f"  Written: {mem_path}")
    else:
        print("  Skipped L2 (enable_l2=false)")

    # Layer 3: bypass_wrapper
    bypass_path = None
    if mem_spec.enable_l3:
        bypass_v = gen_bypass_wrapper(
            mem_spec, ecc_params, interface_type,
            l3_module, l2_module, tiling,
        )
        bypass_path = instance_outdir / f"{l3_module}.v"
        with open(bypass_path, "w", encoding="utf-8") as f:
            f.write(bypass_v)
        print(f"  Written: {bypass_path}")

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(PROJECT_ROOT))
        except ValueError:
            return str(p)

    output_files = [_rel(phy_path)]
    if mem_path is not None:
        output_files.append(_rel(mem_path))
    if bypass_path is not None:
        output_files.append(_rel(bypass_path))
    if ecc_params.enabled:
        enc_file = instance_outdir / f"{ecc_modules.enc_module}.sv"
        dec_file = instance_outdir / f"{ecc_modules.dec_module}.sv"
        output_files.extend([_rel(enc_file), _rel(dec_file)])

    total_latency = (
        mem_spec.input_pipe_stages
        + mem_spec.ram_rd_latency
        + mem_spec.ecc_pipe_stages
        + mem_spec.output_pipe_stages
    )

    return {
        "top_name": top_name,
        "config_hash": config_hash,
        "name": mem_spec.name,
        "output_dir": mem_spec.output_dir,
        "ecc": ecc_report,
        "physical": {
            "col_count": tiling.col_count,
            "row_count": tiling.row_count,
            "total_blocks": tiling.total_blocks,
            "width_pad_bits": tiling.width_pad_bits,
        },
        "total_read_latency": {
            "input_pipe": mem_spec.input_pipe_stages,
            "ram_rd_latency": mem_spec.ram_rd_latency,
            "ecc_pipe": mem_spec.ecc_pipe_stages,
            "output_pipe": mem_spec.output_pipe_stages,
            "total": total_latency,
        },
        "output_files": output_files,
    }


if __name__ == "__main__":
    main()
