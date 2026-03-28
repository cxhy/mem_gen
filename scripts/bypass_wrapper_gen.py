"""Layer 3: bypass_wrapper Verilog generation (Jinja2 template).

Solves read-after-write hazard for sync dual_port memories (1r1w, 1r1wm).
When write and read target the same address in the same cycle, the read output
returns the new write data instead of the stale SRAM value.

Only supports: 1r1w, 1r1wm (sync dual_port without async).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

import jinja2

from config_io import EccParams, InterfaceType, MemorySpec, TilingParams
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

_bypass_wrapper_tmpl = _env.get_template("bypass_wrapper.v.j2")


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class BypassWrapperGenerator(ABC):
    """Base class for bypass wrapper generators."""

    @abstractmethod
    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      interface_type: InterfaceType,
                      module_name: str, l2_wrapper_name: str,
                      tiling: TilingParams) -> dict:
        ...

    def generate(self, mem_spec: MemorySpec, ecc_params: EccParams,
                 interface_type: InterfaceType,
                 module_name: str, l2_wrapper_name: str,
                 tiling: TilingParams) -> str:
        ctx = self.build_context(mem_spec, ecc_params, interface_type,
                                 module_name, l2_wrapper_name, tiling)
        return _bypass_wrapper_tmpl.render(ctx)


# ---------------------------------------------------------------------------
# DualPortBypassGen — 1r1w, 1r1wm
# ---------------------------------------------------------------------------

class DualPortBypassGen(BypassWrapperGenerator):

    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      interface_type: InterfaceType,
                      module_name: str, l2_wrapper_name: str,
                      tiling: TilingParams) -> dict:
        has_ecc = ecc_params.enabled
        has_mask = interface_type.has_mask
        addr_width = clog2(mem_spec.depth)
        detailed_report = mem_spec.ecc.detailed_report if has_ecc else False

        bypass_depth = (
            mem_spec.input_pipe_stages
            + mem_spec.ram_rd_latency
            + mem_spec.ecc_pipe_stages
            + mem_spec.output_pipe_stages
        )

        # -- Module ports --
        ports: list[str] = [
            "input                          i_clk",
            "input                          i_rst_n",
        ]
        if has_ecc:
            ports.append("// ECC control")
            ports.append("input                          i_ecc_en")
            ports.append("input  [1:0]                   i_ecc_err_insert")
            ports.append("input  [1:0]                   i_ecc_err_mask")
        ports.append("// Init")
        ports.append("input                          i_init_en")
        ports.append("input                          i_init_value")
        ports.append("output                         o_init_done")
        ports.append("// Write port")
        ports.append("input                          i_wr_en")
        ports.append("input  [ADDR_WIDTH-1:0]        i_wr_addr")
        ports.append("input  [DATA_WIDTH-1:0]        i_wr_data")
        if has_mask:
            mask_w = mem_spec.width // tiling.mask_gran
            ports.append(f"input  [{mask_w}-1:0]        i_wr_bwen")
        ports.append("// Read port")
        ports.append("input                          i_rd_en")
        ports.append("input  [ADDR_WIDTH-1:0]        i_rd_addr")
        ports.append("output [DATA_WIDTH-1:0]        o_rd_data")
        if has_ecc:
            ports.append("// ECC status")
            ports.append("output                         o_ecc_correctable_valid")
            ports.append("output [ADDR_WIDTH-1:0]        o_ecc_correctable_addr")
            ports.append("output                         o_ecc_uncorrectable_valid")
            ports.append("output [ADDR_WIDTH-1:0]        o_ecc_uncorrectable_addr")
            if detailed_report:
                ecc_m = ecc_params.m
                ports.append(f"output [{ecc_m}-1:0]        o_ecc_err_syndrome")

        # -- L2 instance ports --
        l2_inst_ports: list[str] = [
            ".i_clk            (i_clk)",
            ".i_rst_n          (i_rst_n)",
        ]
        if has_ecc:
            l2_inst_ports.extend([
                ".i_ecc_en         (i_ecc_en)",
                ".i_ecc_err_insert (i_ecc_err_insert)",
                ".i_ecc_err_mask   (i_ecc_err_mask)",
            ])
        l2_inst_ports.extend([
            ".i_init_en        (i_init_en)",
            ".i_init_value     (i_init_value)",
            ".o_init_done      (o_init_done)",
            ".i_wr_en          (i_wr_en)",
            ".i_wr_addr        (i_wr_addr)",
            ".i_wr_data        (i_wr_data)",
        ])
        if has_mask:
            l2_inst_ports.append(".i_wr_bwen        (i_wr_bwen)")
        l2_inst_ports.extend([
            ".i_rd_en          (i_rd_en)",
            ".i_rd_addr        (i_rd_addr)",
            ".o_rd_data        (l2_rd_data)",
        ])
        if has_ecc:
            l2_inst_ports.extend([
                ".o_ecc_correctable_valid   (l2_ecc_correctable_valid)",
                ".o_ecc_correctable_addr    (l2_ecc_correctable_addr)",
                ".o_ecc_uncorrectable_valid (l2_ecc_uncorrectable_valid)",
                ".o_ecc_uncorrectable_addr  (l2_ecc_uncorrectable_addr)",
            ])
            if detailed_report:
                l2_inst_ports.append(
                    ".o_ecc_err_syndrome       (l2_ecc_err_syndrome)"
                )

        # -- Bypass pipeline width --
        # bypass_entry = {hit, wr_data [, wr_bwen]}
        bypass_data_width = 1 + mem_spec.width
        if has_mask:
            bypass_data_width += mask_w

        ctx: dict = {
            "module_name": module_name,
            "description": f"Bypass wrapper for {mem_spec.name} ({mem_spec.type})",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "data_width": mem_spec.width,
            "ram_depth": mem_spec.depth,
            "addr_width": addr_width,
            "has_ecc": has_ecc,
            "has_mask": has_mask,
            "detailed_report": detailed_report,
            "bypass_depth": bypass_depth,
            "bypass_data_width": bypass_data_width,
            "l2_wrapper_name": l2_wrapper_name,
            "module_ports": ports,
            "l2_inst_ports": l2_inst_ports,
        }

        if has_ecc:
            ctx["ecc_m"] = ecc_params.m

        if has_mask:
            ctx["mask_width"] = mask_w
            ctx["mask_gran"] = tiling.mask_gran

        return ctx


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

GENERATORS: dict[str, BypassWrapperGenerator] = {
    "dual_port": DualPortBypassGen(),
}


def gen_bypass_wrapper(mem_spec: MemorySpec, ecc_params: EccParams,
                       interface_type: InterfaceType,
                       module_name: str, l2_wrapper_name: str,
                       tiling: TilingParams) -> str:
    """Entry point — dispatch to the correct generator by base_type."""
    if interface_type.is_async:
        raise ValueError(
            "gen_bypass_wrapper: async dual_port is not supported. "
            "Only sync dual_port (1r1w, 1r1wm) is supported."
        )
    base_type = interface_type.base_type
    if base_type not in GENERATORS:
        raise ValueError(
            f"L3 bypass_wrapper not supported for base_type '{base_type}'. "
            f"Only sync dual_port (1r1w, 1r1wm) is supported."
        )
    generator = GENERATORS[base_type]
    return generator.generate(mem_spec, ecc_params, interface_type,
                              module_name, l2_wrapper_name, tiling)
