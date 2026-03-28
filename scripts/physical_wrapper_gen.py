"""Layer 1: physical_wrapper Verilog generation (Jinja2 template).

Dispatch by interface_type name (9 types → 4 context builders):
  SinglePort:    1rw, 1rwm
  DualPort:      1r1w, 1r1wm, 1r1wa, 1r1wma
  TrueDualPort:  2rw, 2rwm
  Rom:           rom
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
import jinja2

from config_io import (
    EccParams, InterfaceType, MemorySpec, SubTypeInfo, TilingParams,
    pin_connect, resolve_sub_type,
)
from verilog_utils import clog2


# ---------------------------------------------------------------------------
# Jinja2 environment (module-level singleton)
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
    undefined=jinja2.StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)

_physical_wrapper_tmpl = _env.get_template("physical_wrapper.v.j2")


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class PhysicalWrapperGenerator(ABC):
    """Base class for physical wrapper generators."""

    # -- Tiling calculation --------------------------------------------------

    @staticmethod
    def calc_tiling(logical_width: int, depth: int,
                    lib_width: int, lib_depth: int,
                    lib_mask_width: int = 0) -> TilingParams:
        col_count = math.ceil(logical_width / lib_width)
        row_count = math.ceil(depth / lib_depth)
        total_blocks = col_count * row_count
        width_pad_bits = col_count * lib_width - logical_width

        mask_pad_bits = 0
        total_mask_width = 0
        mask_gran = 1
        if lib_mask_width > 0:
            last_col_data_bits = logical_width - (col_count - 1) * lib_width
            if last_col_data_bits < lib_width and last_col_data_bits > 0:
                last_col_mask = math.ceil(last_col_data_bits * lib_mask_width / lib_width)
                mask_pad_bits = lib_mask_width - last_col_mask
            else:
                mask_pad_bits = 0
            total_mask_width = col_count * lib_mask_width
            mask_gran = lib_width // lib_mask_width

        return TilingParams(
            col_count=col_count,
            row_count=row_count,
            total_blocks=total_blocks,
            width_pad_bits=width_pad_bits,
            mask_pad_bits=mask_pad_bits,
            total_mask_width=total_mask_width,
            mask_gran=mask_gran,
        )

    # -- Abstract context builder --------------------------------------------

    @abstractmethod
    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      tiling: TilingParams, interface_type: InterfaceType,
                      sub_type_info: SubTypeInfo, module_name: str) -> dict:
        ...

    # -- Render (final) ------------------------------------------------------

    def generate(self, mem_spec: MemorySpec, ecc_params: EccParams,
                 tiling: TilingParams, interface_type: InterfaceType,
                 sub_type_info: SubTypeInfo, module_name: str) -> str:
        ctx = self.build_context(
            mem_spec, ecc_params, tiling, interface_type, sub_type_info,
            module_name)
        return _physical_wrapper_tmpl.render(ctx)

    # -- Shared helpers for context building ---------------------------------

    @staticmethod
    def _format_const_value(value: object, lib_width: int) -> str:
        if value == "zeros":
            return f"{{{lib_width}{{1'b0}}}}"
        if value == "ones":
            return f"{{{lib_width}{{1'b1}}}}"
        return str(value)

    @staticmethod
    def _build_cell_ports(func_ports: list[str], sub_type_info: SubTypeInfo,
                          lib_width: int) -> list[str]:
        const_lines = [
            f".{pin} ({PhysicalWrapperGenerator._format_const_value(val, lib_width)})"
            for pin, val in sub_type_info.const_ports.items()
        ]
        output_lines = [f".{pin} ()" for pin in sub_type_info.output_ports]
        return func_ports + const_lines + output_lines

    @staticmethod
    def _data_slice_expr(col: int, col_count: int, lib_width: int,
                         data_width: int, width_pad_bits: int,
                         data_signal: str) -> str:
        bit_lo = col * lib_width
        bit_hi = bit_lo + lib_width - 1
        if col == col_count - 1 and width_pad_bits > 0:
            return f"{{{width_pad_bits}'b0, {data_signal}[{data_width - 1}:{bit_lo}]}}"
        return f"{data_signal}[{bit_hi}:{bit_lo}]"

    @staticmethod
    def _mask_slice_expr(col: int, lib_mask_width: int,
                         mask_signal: str) -> str:
        bit_lo = col * lib_mask_width
        bit_hi = bit_lo + lib_mask_width - 1
        return f"{mask_signal}[{bit_hi}:{bit_lo}]"

    @staticmethod
    def _mask_expand_block(wire_name: str, mask_base_signal: str,
                           mask_offset: int, lib_width: int,
                           lib_mask_width: int, mask_gran: int,
                           row: int, col: int,
                           port_tag: str = "") -> dict:
        """Build a descriptor for coarse-mask → bit-level expansion (RTL generate block)."""
        label_tag = f"R{row}_C{col}" + (f"_{port_tag.upper()}" if port_tag else "")
        genvar_tag = f"r{row}_c{col}" + (f"_{port_tag}" if port_tag else "")
        return {
            "wire_name": wire_name,
            "mask_base_signal": mask_base_signal,
            "mask_offset": mask_offset,
            "lib_width": lib_width,
            "lib_mask_width": lib_mask_width,
            "mask_gran": mask_gran,
            "genvar_name": f"g_bwen_{genvar_tag}",
            "label": f"G_MASK_EXP_{label_tag}",
        }

    # -- Common context fields -----------------------------------------------

    @staticmethod
    def _base_context(mem_spec: MemorySpec, ecc_params: EccParams,
                      tiling: TilingParams, module_name: str) -> dict:
        """Build the shared scalar fields for any generator."""
        data_width = ecc_params.logical_total_width
        col_count = tiling.col_count
        row_count = tiling.row_count
        lib_width = mem_spec.physical.lib_width
        row_sel_width = clog2(row_count) if row_count > 1 else 0

        return {
            "module_name": module_name,
            "description": f"Physical wrapper for {mem_spec.type} memory",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "data_width": data_width,
            "addr_width": clog2(mem_spec.depth),
            "lib_name": mem_spec.physical.lib_name,
            "lib_addr_width": clog2(mem_spec.physical.lib_depth),
            "lib_width": lib_width,
            "col_count": col_count,
            "row_count": row_count,
            "row_sel_width": row_sel_width,
            "total_phy_width": col_count * lib_width,
            "width_pad_bits": tiling.width_pad_bits,
            "total_mask_width": tiling.total_mask_width,
            "lib_mask_width": mem_spec.physical.lib_mask_width,
            "mask_gran": tiling.mask_gran,
            "sim_depth": mem_spec.depth,
        }


# ---------------------------------------------------------------------------
# SinglePortGenerator — 1rw, 1rwm
# ---------------------------------------------------------------------------

class SinglePortGenerator(PhysicalWrapperGenerator):

    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      tiling: TilingParams, interface_type: InterfaceType,
                      sub_type_info: SubTypeInfo, module_name: str) -> dict:
        ctx = self._base_context(mem_spec, ecc_params, tiling, module_name)
        port_map = interface_type.port_map
        has_mask = interface_type.has_mask
        row_count = ctx["row_count"]
        col_count = ctx["col_count"]
        row_sel_width = ctx["row_sel_width"]
        data_width = ctx["data_width"]
        lib_width = ctx["lib_width"]
        width_pad_bits = ctx["width_pad_bits"]
        total_mask_width = ctx["total_mask_width"]

        # Module ports
        module_ports = [
            "input                          clk",
            "input                          cen",
            "input                          wen",
            "input  [ADDR_WIDTH-1:0]        addr",
            "input  [DATA_WIDTH-1:0]        wdata",
        ]
        if has_mask:
            module_ports.append(f"input  [{total_mask_width}-1:0]        bwen")
        module_ports.append("output [DATA_WIDTH-1:0]        rdata")

        # Internal wires
        internal_wires = []
        if row_count > 1:
            internal_wires.append(
                "wire [ROW_SEL_WIDTH-1:0]  row_sel  = addr[ADDR_WIDTH-1 -: ROW_SEL_WIDTH];")
        internal_wires.append(
            "wire [LIB_ADDR_WIDTH-1:0] lib_addr = addr[LIB_ADDR_WIDTH-1:0];")

        # Row select registers (update only on valid read)
        row_sel_regs = []
        if row_count > 1:
            row_sel_regs.append(
                {"clk": "clk", "input": "row_sel", "name": "rd_row_sel_d",
                 "enable": "cen & ~wen"})

        # Cell instances
        lib_mask_width_val = mem_spec.physical.lib_mask_width
        mask_gran_val = tiling.mask_gran
        cells = []
        mask_expand_blocks: list[dict] = []
        for row in range(row_count):
            for col in range(col_count):
                bit_lo = col * lib_width
                bit_hi = bit_lo + lib_width - 1

                cen_expr = (f"cen & (row_sel == {row_sel_width}'d{row})"
                            if row_count > 1 else "cen")

                data_slice = self._data_slice_expr(
                    col, col_count, lib_width, data_width,
                    width_pad_bits, "wdata")
                rd_data_wire = f"row_{row}_rd_data[{bit_hi}:{bit_lo}]"

                func_ports = [
                    pin_connect(port_map['clk'],   "clk"),
                    pin_connect(port_map['cen'],   cen_expr),
                    pin_connect(port_map['wen'],   "wen"),
                    pin_connect(port_map['addr'],  "lib_addr"),
                    pin_connect(port_map['wdata'], data_slice),
                    pin_connect(port_map['rdata'], rd_data_wire),
                ]
                if has_mask:
                    if mask_gran_val > 1:
                        wire_name = f"expanded_bwen_r{row}_c{col}"
                        mask_expand_blocks.append(self._mask_expand_block(
                            wire_name, "bwen",
                            col * lib_mask_width_val,
                            lib_width, lib_mask_width_val, mask_gran_val,
                            row, col,
                        ))
                        mask_conn = wire_name
                    else:
                        mask_conn = self._mask_slice_expr(
                            col, lib_mask_width_val, "bwen")
                    func_ports.append(pin_connect(port_map['bwen'], mask_conn))

                all_ports = self._build_cell_ports(
                    func_ports, sub_type_info, lib_width)
                cells.append({
                    "inst_name": f"u_mem_r{row}_c{col}",
                    "ports": all_ports,
                })

        ctx.update({
            "module_ports": module_ports,
            "internal_wires": internal_wires,
            "row_sel_regs": row_sel_regs,
            "rd_data_wire_groups": [{"prefix": ""}],
            "cells": cells,
            "mask_expand_blocks": mask_expand_blocks,
            "read_muxes": [{
                "sel_signal": "rd_row_sel_d",
                "wire_prefix": "",
                "rd_data_name": "rdata",
            }],
            "sim_mode": "rw",
            "sim_has_mask": has_mask,
            "sim_mask_gran": mask_gran_val,
            "sim_is_async": False,
        })
        return ctx


# ---------------------------------------------------------------------------
# DualPortGenerator — 1r1w, 1r1wm, 1r1wa, 1r1wma
# ---------------------------------------------------------------------------

class DualPortGenerator(PhysicalWrapperGenerator):

    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      tiling: TilingParams, interface_type: InterfaceType,
                      sub_type_info: SubTypeInfo, module_name: str) -> dict:
        ctx = self._base_context(mem_spec, ecc_params, tiling, module_name)
        port_map = interface_type.port_map
        has_mask = interface_type.has_mask
        is_async = interface_type.is_async
        row_count = ctx["row_count"]
        col_count = ctx["col_count"]
        row_sel_width = ctx["row_sel_width"]
        data_width = ctx["data_width"]
        lib_width = ctx["lib_width"]
        width_pad_bits = ctx["width_pad_bits"]
        total_mask_width = ctx["total_mask_width"]

        # Module ports
        module_ports = []
        if is_async:
            module_ports.append("input                          wr_clk")
            module_ports.append("input                          rd_clk")
        else:
            module_ports.append("input                          clk")
        module_ports.extend([
            "input                          wr_en",
            "input  [ADDR_WIDTH-1:0]        wr_addr",
            "input  [DATA_WIDTH-1:0]        wr_data",
        ])
        if has_mask:
            module_ports.append(f"input  [{total_mask_width}-1:0]        wr_mask")
        module_ports.extend([
            "input                          rd_en",
            "input  [ADDR_WIDTH-1:0]        rd_addr",
            "output [DATA_WIDTH-1:0]        rd_data",
        ])

        # Internal wires
        internal_wires = []
        if row_count > 1:
            internal_wires.append(
                "wire [ROW_SEL_WIDTH-1:0]  wr_row_sel  = wr_addr[ADDR_WIDTH-1 -: ROW_SEL_WIDTH];")
            internal_wires.append(
                "wire [ROW_SEL_WIDTH-1:0]  rd_row_sel  = rd_addr[ADDR_WIDTH-1 -: ROW_SEL_WIDTH];")
        internal_wires.append(
            "wire [LIB_ADDR_WIDTH-1:0] wr_lib_addr = wr_addr[LIB_ADDR_WIDTH-1:0];")
        internal_wires.append(
            "wire [LIB_ADDR_WIDTH-1:0] rd_lib_addr = rd_addr[LIB_ADDR_WIDTH-1:0];")

        # Row select registers (update only on valid read)
        row_sel_regs = []
        if row_count > 1:
            rd_clk_name = "rd_clk" if is_async else "clk"
            row_sel_regs.append(
                {"clk": rd_clk_name, "input": "rd_row_sel", "name": "rd_row_sel_d",
                 "enable": "rd_en"})

        # Cell instances
        lib_mask_width_val = mem_spec.physical.lib_mask_width
        mask_gran_val = tiling.mask_gran
        cells = []
        mask_expand_blocks: list[dict] = []
        for row in range(row_count):
            for col in range(col_count):
                bit_lo = col * lib_width
                bit_hi = bit_lo + lib_width - 1

                wr_en_expr = (f"wr_en & (wr_row_sel == {row_sel_width}'d{row})"
                              if row_count > 1 else "wr_en")
                rd_en_expr = (f"rd_en & (rd_row_sel == {row_sel_width}'d{row})"
                              if row_count > 1 else "rd_en")

                data_slice = self._data_slice_expr(
                    col, col_count, lib_width, data_width,
                    width_pad_bits, "wr_data")
                rd_data_wire = f"row_{row}_rd_data[{bit_hi}:{bit_lo}]"

                func_ports: list[str] = []
                if is_async:
                    func_ports.append(pin_connect(port_map['wr_clk'], "wr_clk"))
                    func_ports.append(pin_connect(port_map['rd_clk'], "rd_clk"))
                else:
                    func_ports.append(pin_connect(port_map['clk'], "clk"))

                func_ports.extend([
                    pin_connect(port_map['wr_en'],   wr_en_expr),
                    pin_connect(port_map['wr_addr'], "wr_lib_addr"),
                    pin_connect(port_map['wr_data'], data_slice),
                    pin_connect(port_map['rd_en'],   rd_en_expr),
                    pin_connect(port_map['rd_addr'], "rd_lib_addr"),
                    pin_connect(port_map['rd_data'], rd_data_wire),
                ])
                if has_mask:
                    if mask_gran_val > 1:
                        wire_name = f"expanded_bwen_r{row}_c{col}"
                        mask_expand_blocks.append(self._mask_expand_block(
                            wire_name, "wr_mask",
                            col * lib_mask_width_val,
                            lib_width, lib_mask_width_val, mask_gran_val,
                            row, col,
                        ))
                        mask_conn = wire_name
                    else:
                        mask_conn = self._mask_slice_expr(
                            col, lib_mask_width_val, "wr_mask")
                    func_ports.append(pin_connect(port_map['wr_mask'], mask_conn))

                all_ports = self._build_cell_ports(
                    func_ports, sub_type_info, lib_width)
                cells.append({
                    "inst_name": f"u_mem_r{row}_c{col}",
                    "ports": all_ports,
                })

        ctx.update({
            "module_ports": module_ports,
            "internal_wires": internal_wires,
            "row_sel_regs": row_sel_regs,
            "rd_data_wire_groups": [{"prefix": ""}],
            "cells": cells,
            "mask_expand_blocks": mask_expand_blocks,
            "read_muxes": [{
                "sel_signal": "rd_row_sel_d",
                "wire_prefix": "",
                "rd_data_name": "rd_data",
            }],
            "sim_mode": "dp",
            "sim_has_mask": has_mask,
            "sim_mask_gran": mask_gran_val,
            "sim_is_async": is_async,
        })
        return ctx


# ---------------------------------------------------------------------------
# RomGenerator — rom
# ---------------------------------------------------------------------------

class RomGenerator(PhysicalWrapperGenerator):

    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      tiling: TilingParams, interface_type: InterfaceType,
                      sub_type_info: SubTypeInfo, module_name: str) -> dict:
        ctx = self._base_context(mem_spec, ecc_params, tiling, module_name)
        port_map = interface_type.port_map
        row_count = ctx["row_count"]
        col_count = ctx["col_count"]
        row_sel_width = ctx["row_sel_width"]
        lib_width = ctx["lib_width"]

        # Module ports
        module_ports = [
            "input                          clk",
            "input                          cen",
            "input  [ADDR_WIDTH-1:0]        addr",
            "output [DATA_WIDTH-1:0]        rdata",
        ]

        # Internal wires
        internal_wires = []
        if row_count > 1:
            internal_wires.append(
                "wire [ROW_SEL_WIDTH-1:0]  row_sel  = addr[ADDR_WIDTH-1 -: ROW_SEL_WIDTH];")
        internal_wires.append(
            "wire [LIB_ADDR_WIDTH-1:0] lib_addr = addr[LIB_ADDR_WIDTH-1:0];")

        # Row select registers (update only on valid read)
        row_sel_regs = []
        if row_count > 1:
            row_sel_regs.append(
                {"clk": "clk", "input": "row_sel", "name": "rd_row_sel_d",
                 "enable": "cen"})

        # Cell instances
        cells = []
        for row in range(row_count):
            for col in range(col_count):
                bit_lo = col * lib_width
                bit_hi = bit_lo + lib_width - 1

                cen_expr = (f"cen & (row_sel == {row_sel_width}'d{row})"
                            if row_count > 1 else "cen")
                rd_data_wire = f"row_{row}_rd_data[{bit_hi}:{bit_lo}]"

                func_ports = [
                    pin_connect(port_map['clk'],   "clk"),
                    pin_connect(port_map['cen'],   cen_expr),
                    pin_connect(port_map['addr'],  "lib_addr"),
                    pin_connect(port_map['rdata'], rd_data_wire),
                ]

                all_ports = self._build_cell_ports(
                    func_ports, sub_type_info, lib_width)
                cells.append({
                    "inst_name": f"u_mem_r{row}_c{col}",
                    "ports": all_ports,
                })

        ctx.update({
            "module_ports": module_ports,
            "internal_wires": internal_wires,
            "row_sel_regs": row_sel_regs,
            "rd_data_wire_groups": [{"prefix": ""}],
            "cells": cells,
            "mask_expand_blocks": [],
            "read_muxes": [{
                "sel_signal": "rd_row_sel_d",
                "wire_prefix": "",
                "rd_data_name": "rdata",
            }],
            "sim_mode": "rom",
            "sim_has_mask": False,
            "sim_mask_gran": 1,
            "sim_is_async": False,
        })
        return ctx


# ---------------------------------------------------------------------------
# TrueDualPortGenerator — 2rw, 2rwm
# ---------------------------------------------------------------------------

class TrueDualPortGenerator(PhysicalWrapperGenerator):

    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      tiling: TilingParams, interface_type: InterfaceType,
                      sub_type_info: SubTypeInfo, module_name: str) -> dict:
        ctx = self._base_context(mem_spec, ecc_params, tiling, module_name)
        port_map = interface_type.port_map
        has_mask = interface_type.has_mask
        row_count = ctx["row_count"]
        col_count = ctx["col_count"]
        row_sel_width = ctx["row_sel_width"]
        data_width = ctx["data_width"]
        lib_width = ctx["lib_width"]
        width_pad_bits = ctx["width_pad_bits"]
        total_mask_width = ctx["total_mask_width"]

        # Module ports
        module_ports = [
            "input                          a_clk",
            "input                          a_cen",
            "input                          a_wen",
            "input  [ADDR_WIDTH-1:0]        a_addr",
            "input  [DATA_WIDTH-1:0]        a_wdata",
        ]
        if has_mask:
            module_ports.append(f"input  [{total_mask_width}-1:0]        a_bwen")
        module_ports.extend([
            "output [DATA_WIDTH-1:0]        a_rdata",
            "input                          b_clk",
            "input                          b_cen",
            "input                          b_wen",
            "input  [ADDR_WIDTH-1:0]        b_addr",
            "input  [DATA_WIDTH-1:0]        b_wdata",
        ])
        if has_mask:
            module_ports.append(f"input  [{total_mask_width}-1:0]        b_bwen")
        module_ports.append("output [DATA_WIDTH-1:0]        b_rdata")

        # Internal wires
        internal_wires = []
        if row_count > 1:
            internal_wires.append(
                "wire [ROW_SEL_WIDTH-1:0]  a_row_sel  = a_addr[ADDR_WIDTH-1 -: ROW_SEL_WIDTH];")
            internal_wires.append(
                "wire [ROW_SEL_WIDTH-1:0]  b_row_sel  = b_addr[ADDR_WIDTH-1 -: ROW_SEL_WIDTH];")
        internal_wires.append(
            "wire [LIB_ADDR_WIDTH-1:0] a_lib_addr = a_addr[LIB_ADDR_WIDTH-1:0];")
        internal_wires.append(
            "wire [LIB_ADDR_WIDTH-1:0] b_lib_addr = b_addr[LIB_ADDR_WIDTH-1:0];")

        # Row select registers — A and B each on own clock (update only on valid read)
        row_sel_regs = []
        if row_count > 1:
            row_sel_regs.append(
                {"clk": "a_clk", "input": "a_row_sel", "name": "a_rd_row_sel_d",
                 "enable": "a_cen & ~a_wen"})
            row_sel_regs.append(
                {"clk": "b_clk", "input": "b_row_sel", "name": "b_rd_row_sel_d",
                 "enable": "b_cen & ~b_wen"})

        # Cell instances
        lib_mask_width_val = mem_spec.physical.lib_mask_width
        mask_gran_val = tiling.mask_gran
        cells = []
        mask_expand_blocks: list[dict] = []
        for row in range(row_count):
            for col in range(col_count):
                bit_lo = col * lib_width
                bit_hi = bit_lo + lib_width - 1

                a_cen_expr = (f"a_cen & (a_row_sel == {row_sel_width}'d{row})"
                              if row_count > 1 else "a_cen")
                b_cen_expr = (f"b_cen & (b_row_sel == {row_sel_width}'d{row})"
                              if row_count > 1 else "b_cen")

                a_data_slice = self._data_slice_expr(
                    col, col_count, lib_width, data_width,
                    width_pad_bits, "a_wdata")
                b_data_slice = self._data_slice_expr(
                    col, col_count, lib_width, data_width,
                    width_pad_bits, "b_wdata")
                a_rd_wire = f"a_row_{row}_rd_data[{bit_hi}:{bit_lo}]"
                b_rd_wire = f"b_row_{row}_rd_data[{bit_hi}:{bit_lo}]"

                func_ports: list[str] = []

                # Port A
                func_ports.append(pin_connect(port_map['a_clk'],   "a_clk"))
                func_ports.append(pin_connect(port_map['a_cen'],   a_cen_expr))
                func_ports.append(pin_connect(port_map['a_wen'],   "a_wen"))
                func_ports.append(pin_connect(port_map['a_addr'],  "a_lib_addr"))
                func_ports.append(pin_connect(port_map['a_wdata'], a_data_slice))
                func_ports.append(pin_connect(port_map['a_rdata'], a_rd_wire))
                if has_mask:
                    if mask_gran_val > 1:
                        a_wire = f"a_expanded_bwen_r{row}_c{col}"
                        mask_expand_blocks.append(self._mask_expand_block(
                            a_wire, "a_bwen",
                            col * lib_mask_width_val,
                            lib_width, lib_mask_width_val, mask_gran_val,
                            row, col, port_tag="a",
                        ))
                        a_mask_conn = a_wire
                    else:
                        a_mask_conn = self._mask_slice_expr(
                            col, lib_mask_width_val, "a_bwen")
                    func_ports.append(pin_connect(port_map['a_bwen'], a_mask_conn))

                # Port B
                func_ports.append(pin_connect(port_map['b_clk'],   "b_clk"))
                func_ports.append(pin_connect(port_map['b_cen'],   b_cen_expr))
                func_ports.append(pin_connect(port_map['b_wen'],   "b_wen"))
                func_ports.append(pin_connect(port_map['b_addr'],  "b_lib_addr"))
                func_ports.append(pin_connect(port_map['b_wdata'], b_data_slice))
                func_ports.append(pin_connect(port_map['b_rdata'], b_rd_wire))
                if has_mask:
                    if mask_gran_val > 1:
                        b_wire = f"b_expanded_bwen_r{row}_c{col}"
                        mask_expand_blocks.append(self._mask_expand_block(
                            b_wire, "b_bwen",
                            col * lib_mask_width_val,
                            lib_width, lib_mask_width_val, mask_gran_val,
                            row, col, port_tag="b",
                        ))
                        b_mask_conn = b_wire
                    else:
                        b_mask_conn = self._mask_slice_expr(
                            col, lib_mask_width_val, "b_bwen")
                    func_ports.append(pin_connect(port_map['b_bwen'], b_mask_conn))

                all_ports = self._build_cell_ports(
                    func_ports, sub_type_info, lib_width)
                cells.append({
                    "inst_name": f"u_mem_r{row}_c{col}",
                    "ports": all_ports,
                })

        ctx.update({
            "module_ports": module_ports,
            "internal_wires": internal_wires,
            "row_sel_regs": row_sel_regs,
            "rd_data_wire_groups": [{"prefix": "a_"}, {"prefix": "b_"}],
            "cells": cells,
            "mask_expand_blocks": mask_expand_blocks,
            "read_muxes": [
                {
                    "sel_signal": "a_rd_row_sel_d",
                    "wire_prefix": "a_",
                    "rd_data_name": "a_rdata",
                },
                {
                    "sel_signal": "b_rd_row_sel_d",
                    "wire_prefix": "b_",
                    "rd_data_name": "b_rdata",
                },
            ],
            "sim_mode": "tdp",
            "sim_has_mask": has_mask,
            "sim_mask_gran": mask_gran_val,
            "sim_is_async": False,
        })
        return ctx


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

GENERATORS: dict[str, PhysicalWrapperGenerator] = {
    "single_port":    SinglePortGenerator(),
    "dual_port":      DualPortGenerator(),
    "true_dual_port": TrueDualPortGenerator(),
    "rom":            RomGenerator(),
}


def gen_physical_wrapper(mem_spec: MemorySpec, ecc_params: EccParams,
                         tiling: TilingParams,
                         interface_type: InterfaceType,
                         module_name: str) -> str:
    """Entry point — dispatch to the correct generator by interface_type."""
    sub_type_info = resolve_sub_type(interface_type, mem_spec.physical.sub_type)
    generator = GENERATORS[interface_type.base_type]
    return generator.generate(mem_spec, ecc_params, tiling, interface_type,
                              sub_type_info, module_name)
