"""Vendor behavioral simulation model generator.

Reads mem_config.json + vendor_port_map.json, generates behavioral Verilog
simulation models for each unique lib_name into the vendor/ directory.
Supports all 9 interface_types: 1rw, 1rwm, 1r1w, 1r1wm, 1r1wa, 1r1wma, 2rw, 2rwm, rom.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from config_io import InterfaceType, PhysicalConfig, ProjectConfig, parse_pin
from verilog_utils import file_header


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VendorCellSpec:
    """Specification for a single vendor cell behavioral model."""
    lib_name: str
    mem_type: str            # interface_type key in vendor_port_map (e.g. "1r1w")
    lib_width: int
    lib_depth: int
    lib_mask_width: int      # 0 if no mask


def _addr_width(depth: int) -> int:
    """Compute address width (clog2) for a given depth."""
    if depth <= 1:
        return 1
    return max(1, (depth - 1).bit_length())


# ---------------------------------------------------------------------------
# Collect unique cells from config
# ---------------------------------------------------------------------------

def collect_cells(project_config: ProjectConfig) -> list[VendorCellSpec]:
    """Extract unique lib_name entries from project config.

    Raises ValueError if the same lib_name is used with different interface types.
    """
    seen: dict[str, VendorCellSpec] = {}

    for mem in project_config.memories:
        lib_name = mem.physical.lib_name
        mem_type = mem.type

        if lib_name in seen:
            if seen[lib_name].mem_type != mem_type:
                raise ValueError(
                    f"lib_name '{lib_name}' used with conflicting interface types: "
                    f"'{seen[lib_name].mem_type}' and '{mem_type}'"
                )
            continue

        seen[lib_name] = VendorCellSpec(
            lib_name=lib_name,
            mem_type=mem_type,
            lib_width=mem.physical.lib_width,
            lib_depth=mem.physical.lib_depth,
            lib_mask_width=mem.physical.lib_mask_width,
        )

    return list(seen.values())


# ---------------------------------------------------------------------------
# Pin helpers
# ---------------------------------------------------------------------------

def _pin(port_map_value: str) -> str:
    """Extract vendor pin name from port_map value (strip ~ prefix)."""
    name, _ = parse_pin(port_map_value)
    return name


def _enable_expr(port_map_value: str) -> str:
    """Return Verilog expression that evaluates true when enable is active.

    port_map '~CEB' → CEB is active-low at vendor cell → '!CEB'
    port_map 'CE'   → CE is active-high at vendor cell → 'CE'
    """
    name, inverted = parse_pin(port_map_value)
    return f"!{name}" if inverted else name


def _masked_write_logic(
    bwen_pin: str, bwen_w: int, dw: int,
    addr_pin: str, wdata_pin: str,
) -> str:
    """Generate masked write logic for behavioral model."""
    if bwen_w != dw:
        mask_gran = dw // bwen_w
        return (
            f"                // Expand mask: each BWEN bit controls {mask_gran} data bits\n"
            f"                begin : mask_expand\n"
            f"                    integer i;\n"
            f"                    for (i = 0; i < {bwen_w}; i = i + 1) begin\n"
            f"                        if (!{bwen_pin}[i])\n"
            f"                            mem[{addr_pin}][i*{mask_gran} +: {mask_gran}] <= "
            f"{wdata_pin}[i*{mask_gran} +: {mask_gran}];\n"
            f"                    end\n"
            f"                end"
        )
    return (
        f"                mem[{addr_pin}] <= ({wdata_pin} & ~{bwen_pin})"
        f" | (mem[{addr_pin}] & {bwen_pin});"
    )


# ---------------------------------------------------------------------------
# Verilog generation per base_type
# ---------------------------------------------------------------------------

def _gen_single_port(cell: VendorCellSpec, itype: InterfaceType) -> str:
    """Generate behavioral model for single_port (1rw / 1rwm).

    Behavior:
      - CEB=0, WEB=0: write (with optional BWEB mask — active-low)
      - CEB=0, WEB=1: read
    """
    p = itype.port_map
    aw = _addr_width(cell.lib_depth)
    dw = cell.lib_width

    clk_pin   = _pin(p["clk"])
    cen_val   = p["cen"]
    wen_val   = p["wen"]
    addr_pin  = _pin(p["addr"])
    wdata_pin = _pin(p["wdata"])
    rdata_pin = _pin(p["rdata"])

    cen_pin = _pin(cen_val)
    wen_pin = _pin(wen_val)
    cen_check = _enable_expr(cen_val)
    wen_check = _enable_expr(wen_val)

    has_bwen = "bwen" in p

    header = file_header(
        cell.lib_name,
        f"Behavioral simulation model — single_port ({'mask' if has_bwen else 'no mask'}), "
        f"{cell.lib_depth}x{dw}",
    )

    # Port declarations
    port_lines = [
        f"    input                       {clk_pin},",
        f"    input                       {cen_pin},",
        f"    input                       {wen_pin},",
        f"    input  [{aw - 1}:0]  {addr_pin},",
        f"    input  [{dw - 1}:0]  {wdata_pin},",
    ]

    if has_bwen:
        bwen_pin = _pin(p["bwen"])
        bwen_w = cell.lib_mask_width if cell.lib_mask_width > 0 else dw
        port_lines.append(f"    input  [{bwen_w - 1}:0]  {bwen_pin},")

    port_lines.append(f"    output reg [{dw - 1}:0]  {rdata_pin}")
    ports_str = "\n".join(port_lines)

    # Write logic
    if has_bwen:
        bwen_pin = _pin(p["bwen"])
        bwen_w = cell.lib_mask_width if cell.lib_mask_width > 0 else dw
        write_logic = _masked_write_logic(bwen_pin, bwen_w, dw, addr_pin, wdata_pin)
    else:
        write_logic = f"                mem[{addr_pin}] <= {wdata_pin};"

    return f"""{header}
module {cell.lib_name} (
{ports_str}
);

    // Memory array
    reg [{dw - 1}:0] mem [0:{cell.lib_depth - 1}];

    always @(posedge {clk_pin}) begin
        if ({cen_check}) begin
            if ({wen_check}) begin
                // Write
{write_logic}
            end else begin
                // Read
                {rdata_pin} <= mem[{addr_pin}];
            end
        end
    end

endmodule
"""


def _gen_dual_port(cell: VendorCellSpec, itype: InterfaceType) -> str:
    """Generate behavioral model for dual_port (1r1w / 1r1wm / 1r1wa / 1r1wma).

    Sync types use single 'clk', async types use 'wr_clk' + 'rd_clk'.
    Behavior:
      - WEN active: write (with optional mask)
      - REN active: read
    """
    p = itype.port_map
    aw = _addr_width(cell.lib_depth)
    dw = cell.lib_width
    is_async = itype.is_async

    wr_en_val   = p["wr_en"]
    wr_addr_pin = _pin(p["wr_addr"])
    wr_data_pin = _pin(p["wr_data"])
    rd_en_val   = p["rd_en"]
    rd_addr_pin = _pin(p["rd_addr"])
    rd_data_pin = _pin(p["rd_data"])

    wr_en_pin   = _pin(wr_en_val)
    rd_en_pin   = _pin(rd_en_val)
    wr_en_check = _enable_expr(wr_en_val)
    rd_en_check = _enable_expr(rd_en_val)

    if is_async:
        wr_clk_pin = _pin(p["wr_clk"])
        rd_clk_pin = _pin(p["rd_clk"])
    else:
        clk_pin = _pin(p["clk"])

    async_str = "async " if is_async else ""
    header = file_header(
        cell.lib_name,
        f"Behavioral simulation model — {async_str}dual_port "
        f"({'mask' if itype.has_mask else 'no mask'}), "
        f"{cell.lib_depth}x{dw}",
    )

    # Port declarations
    port_lines = []
    if is_async:
        port_lines.append(f"    input                       {wr_clk_pin},")
        port_lines.append(f"    input                       {rd_clk_pin},")
    else:
        port_lines.append(f"    input                       {clk_pin},")

    port_lines.extend([
        f"    input                       {wr_en_pin},",
        f"    input  [{aw - 1}:0]  {wr_addr_pin},",
        f"    input  [{dw - 1}:0]  {wr_data_pin},",
    ])

    has_bwen = "wr_mask" in p
    if has_bwen:
        bwen_pin = _pin(p["wr_mask"])
        bwen_w = cell.lib_mask_width if cell.lib_mask_width > 0 else dw
        port_lines.append(f"    input  [{bwen_w - 1}:0]  {bwen_pin},")

    port_lines.extend([
        f"    input                       {rd_en_pin},",
        f"    input  [{aw - 1}:0]  {rd_addr_pin},",
        f"    output reg [{dw - 1}:0]  {rd_data_pin}",
    ])
    ports_str = "\n".join(port_lines)

    # Write logic
    if has_bwen:
        write_logic = _masked_write_logic(
            bwen_pin, bwen_w, dw, wr_addr_pin, wr_data_pin,
        )
    else:
        write_logic = f"                mem[{wr_addr_pin}] <= {wr_data_pin};"

    wr_clk = wr_clk_pin if is_async else clk_pin
    rd_clk = rd_clk_pin if is_async else clk_pin

    return f"""{header}
module {cell.lib_name} (
{ports_str}
);

    // Memory array
    reg [{dw - 1}:0] mem [0:{cell.lib_depth - 1}];

    // Write port
    always @(posedge {wr_clk}) begin
        if ({wr_en_check}) begin
{write_logic}
        end
    end

    // Read port
    always @(posedge {rd_clk}) begin
        if ({rd_en_check}) begin
            {rd_data_pin} <= mem[{rd_addr_pin}];
        end
    end

endmodule
"""


def _gen_true_dual_port(cell: VendorCellSpec, itype: InterfaceType) -> str:
    """Generate behavioral model for true_dual_port (2rw / 2rwm).

    Two independent read/write ports (A and B), each with own clock.
    Behavior per port:
      - CEN active, WEN active: write (with optional mask)
      - CEN active, WEN inactive: read
    """
    p = itype.port_map
    aw = _addr_width(cell.lib_depth)
    dw = cell.lib_width

    # Port A
    a_clk_pin   = _pin(p["a_clk"])
    a_cen_val   = p["a_cen"]
    a_wen_val   = p["a_wen"]
    a_addr_pin  = _pin(p["a_addr"])
    a_wdata_pin = _pin(p["a_wdata"])
    a_rdata_pin = _pin(p["a_rdata"])
    a_cen_pin   = _pin(a_cen_val)
    a_wen_pin   = _pin(a_wen_val)
    a_cen_check = _enable_expr(a_cen_val)
    a_wen_check = _enable_expr(a_wen_val)

    # Port B
    b_clk_pin   = _pin(p["b_clk"])
    b_cen_val   = p["b_cen"]
    b_wen_val   = p["b_wen"]
    b_addr_pin  = _pin(p["b_addr"])
    b_wdata_pin = _pin(p["b_wdata"])
    b_rdata_pin = _pin(p["b_rdata"])
    b_cen_pin   = _pin(b_cen_val)
    b_wen_pin   = _pin(b_wen_val)
    b_cen_check = _enable_expr(b_cen_val)
    b_wen_check = _enable_expr(b_wen_val)

    has_mask = itype.has_mask

    header = file_header(
        cell.lib_name,
        f"Behavioral simulation model — true_dual_port "
        f"({'mask' if has_mask else 'no mask'}), "
        f"{cell.lib_depth}x{dw}",
    )

    # Port declarations
    port_lines = [
        f"    input                       {a_clk_pin},",
        f"    input                       {a_cen_pin},",
        f"    input                       {a_wen_pin},",
        f"    input  [{aw - 1}:0]  {a_addr_pin},",
        f"    input  [{dw - 1}:0]  {a_wdata_pin},",
    ]

    if has_mask:
        a_bwen_pin = _pin(p["a_bwen"])
        b_bwen_pin = _pin(p["b_bwen"])
        bwen_w = cell.lib_mask_width if cell.lib_mask_width > 0 else dw
        port_lines.append(f"    input  [{bwen_w - 1}:0]  {a_bwen_pin},")

    port_lines.extend([
        f"    output reg [{dw - 1}:0]  {a_rdata_pin},",
        f"    input                       {b_clk_pin},",
        f"    input                       {b_cen_pin},",
        f"    input                       {b_wen_pin},",
        f"    input  [{aw - 1}:0]  {b_addr_pin},",
        f"    input  [{dw - 1}:0]  {b_wdata_pin},",
    ])

    if has_mask:
        port_lines.append(f"    input  [{bwen_w - 1}:0]  {b_bwen_pin},")

    port_lines.append(f"    output reg [{dw - 1}:0]  {b_rdata_pin}")
    ports_str = "\n".join(port_lines)

    # Write logic per port
    if has_mask:
        a_write = _masked_write_logic(a_bwen_pin, bwen_w, dw, a_addr_pin, a_wdata_pin)
        b_write = _masked_write_logic(b_bwen_pin, bwen_w, dw, b_addr_pin, b_wdata_pin)
    else:
        a_write = f"                mem[{a_addr_pin}] <= {a_wdata_pin};"
        b_write = f"                mem[{b_addr_pin}] <= {b_wdata_pin};"

    return f"""{header}
module {cell.lib_name} (
{ports_str}
);

    // Memory array
    reg [{dw - 1}:0] mem [0:{cell.lib_depth - 1}];

    // Port A
    always @(posedge {a_clk_pin}) begin
        if ({a_cen_check}) begin
            if ({a_wen_check}) begin
{a_write}
            end else begin
                {a_rdata_pin} <= mem[{a_addr_pin}];
            end
        end
    end

    // Port B
    always @(posedge {b_clk_pin}) begin
        if ({b_cen_check}) begin
            if ({b_wen_check}) begin
{b_write}
            end else begin
                {b_rdata_pin} <= mem[{b_addr_pin}];
            end
        end
    end

endmodule
"""


def _gen_rom(cell: VendorCellSpec, itype: InterfaceType) -> str:
    """Generate behavioral model for rom.

    Behavior:
      - CEB=0: read
    """
    p = itype.port_map
    aw = _addr_width(cell.lib_depth)
    dw = cell.lib_width

    clk_pin   = _pin(p["clk"])
    cen_val   = p["cen"]
    addr_pin  = _pin(p["addr"])
    rdata_pin = _pin(p["rdata"])

    cen_pin   = _pin(cen_val)
    cen_check = _enable_expr(cen_val)

    header = file_header(
        cell.lib_name,
        f"Behavioral simulation model — rom, {cell.lib_depth}x{dw}",
    )

    return f"""{header}
module {cell.lib_name} (
    input                       {clk_pin},
    input                       {cen_pin},
    input  [{aw - 1}:0]  {addr_pin},
    output reg [{dw - 1}:0]  {rdata_pin}
);

    // Memory array
    reg [{dw - 1}:0] mem [0:{cell.lib_depth - 1}];

    always @(posedge {clk_pin}) begin
        if ({cen_check}) begin
            {rdata_pin} <= mem[{addr_pin}];
        end
    end

endmodule
"""


# ---------------------------------------------------------------------------
# Top-level generation API
# ---------------------------------------------------------------------------

_GENERATORS = {
    "single_port": _gen_single_port,
    "dual_port": _gen_dual_port,
    "true_dual_port": _gen_true_dual_port,
    "rom": _gen_rom,
}


def generate_vendor_models(
    project_config: ProjectConfig,
    vendor_dir: Path,
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Generate behavioral Verilog models for all unique lib_names.

    Args:
        project_config: Loaded project configuration.
        vendor_dir: Output directory for vendor models.
        overwrite: If False (default), skip generation for existing files.

    Returns:
        List of generated file paths.
    """
    vendor_dir.mkdir(parents=True, exist_ok=True)
    cells = collect_cells(project_config)

    generated: list[Path] = []
    for cell in cells:
        out_path = vendor_dir / f"{cell.lib_name}.v"

        if out_path.exists() and not overwrite:
            print(f"  Vendor model exists, skipping: {out_path.name}")
            continue

        itype = project_config.vendor_port_map.interface_types[cell.mem_type]
        gen_fn = _GENERATORS.get(itype.base_type)
        if gen_fn is None:
            print(f"  WARNING: No generator for base_type '{itype.base_type}', "
                  f"skipping {cell.lib_name}")
            continue

        verilog = gen_fn(cell, itype)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(verilog)
        print(f"  Generated vendor model: {out_path.name}")
        generated.append(out_path)

    return generated


# ---------------------------------------------------------------------------
# CLI entry point (standalone usage)
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import sys

    SCRIPTS_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = SCRIPTS_DIR.parent
    sys.path.insert(0, str(SCRIPTS_DIR))

    from config_io import ConfigLoader

    parser = argparse.ArgumentParser(description="Vendor SRAM Behavioral Model Generator")
    parser.add_argument(
        "--config-dir",
        default=str(PROJECT_ROOT / "config"),
        help="Directory containing mem_config.json and vendor_port_map.json",
    )
    parser.add_argument(
        "--vendor-dir",
        default=str(PROJECT_ROOT / "vendor"),
        help="Output directory for vendor models",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing vendor model files",
    )
    args = parser.parse_args()

    config_loader = ConfigLoader()
    project_config = config_loader.load(Path(args.config_dir))

    print(f"Generating vendor models for {len(project_config.memories)} memories...")
    generated = generate_vendor_models(
        project_config,
        Path(args.vendor_dir),
        overwrite=args.overwrite,
    )
    print(f"Done. Generated {len(generated)} vendor model(s).")


if __name__ == "__main__":
    main()
