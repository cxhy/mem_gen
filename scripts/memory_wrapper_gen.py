"""Layer 2: memory_wrapper Verilog generation (Jinja2 template).

Dispatch by base_type (9 types → 4 context builders):
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

from config_io import EccModuleInfo, EccParams, InterfaceType, MemorySpec, TilingParams
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

_memory_wrapper_tmpl = _env.get_template("memory_wrapper.v.j2")


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class MemoryWrapperGenerator(ABC):
    """Base class for memory wrapper generators."""

    @abstractmethod
    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      ecc_modules: EccModuleInfo | None,
                      interface_type: InterfaceType,
                      module_name: str, phy_wrapper_name: str,
                      tiling: TilingParams) -> dict:
        ...

    def generate(self, mem_spec: MemorySpec, ecc_params: EccParams,
                 ecc_modules: EccModuleInfo | None,
                 interface_type: InterfaceType,
                 module_name: str, phy_wrapper_name: str,
                 tiling: TilingParams) -> str:
        ctx = self.build_context(mem_spec, ecc_params, ecc_modules,
                                 interface_type, module_name, phy_wrapper_name,
                                 tiling)
        return _memory_wrapper_tmpl.render(ctx)

    # -- Shared helpers -----------------------------------------------------

    @staticmethod
    def _base_context(mem_spec: MemorySpec, ecc_params: EccParams,
                      ecc_modules: EccModuleInfo | None,
                      interface_type: InterfaceType,
                      module_name: str, phy_wrapper_name: str,
                      tiling: TilingParams) -> dict:
        """Build scalar fields shared by all generators."""
        has_ecc = ecc_params.enabled
        has_mask = interface_type.has_mask
        is_async = interface_type.is_async
        has_init = interface_type.base_type != "rom"

        ctx: dict = {
            "module_name": module_name,
            "description": f"Memory wrapper for {mem_spec.name} ({mem_spec.type})",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "data_width": mem_spec.width,
            "ram_depth": mem_spec.depth,
            "ram_rd_latency": mem_spec.ram_rd_latency,
            "addr_width": clog2(mem_spec.depth),
            "has_ecc": has_ecc,
            "has_mask": has_mask,
            "has_init": has_init,
            "is_async": is_async,
            "input_pipe_stages": mem_spec.input_pipe_stages,
            "ecc_pipe_stages": mem_spec.ecc_pipe_stages,
            "output_pipe_stages": mem_spec.output_pipe_stages,
            "phy_wrapper_name": phy_wrapper_name,
            "detailed_report": mem_spec.ecc.detailed_report if has_ecc else False,
        }

        if has_mask:
            mask_gran = tiling.mask_gran
            ctx["mask_width"] = mem_spec.width // mask_gran
            ctx["total_mask_width"] = tiling.total_mask_width
            ctx["mask_pad_bits"] = tiling.total_mask_width - ctx["mask_width"]
            ctx["mask_per_slice"] = 1

        if has_ecc:
            padded_n = ecc_params.n
            if has_mask:
                mask_gran = tiling.mask_gran
                if mask_gran > 1:
                    padded_n = math.ceil(ecc_params.n / mask_gran) * mask_gran
                    ctx["mask_per_slice"] = padded_n // mask_gran
            ctx.update({
                "ecc_slice_dw": ecc_params.k,
                "ecc_slice_with_ecc_dw": ecc_params.n,
                "ecc_padded_slice_dw": padded_n,
                "ecc_pad_per_slice": padded_n - ecc_params.n,
                "ecc_slice_num": ecc_params.slice_count,
                "ecc_m": ecc_params.m,
                "enc_module": ecc_modules.enc_module,
                "dec_module": ecc_modules.dec_module,
            })

        return ctx

    @staticmethod
    def _make_write_path(prefix: str, pipe_wdata: str,
                         pipe_bwen: str | None,
                         has_init_mux: bool, has_ecc: bool) -> dict:
        """Build a write_path entry for the template."""
        bare = prefix.rstrip("_") if prefix else ""
        gv_suffix = f"_{bare}" if bare else ""
        label_suffix = f"_{bare.upper()}" if bare else ""

        wp: dict = {
            "prefix": bare,
            "pipe_wdata": pipe_wdata,
            "pipe_bwen": pipe_bwen,
            "has_init_mux": has_init_mux,
            "genvar_enc": f"g_enc{gv_suffix}",
            "enc_gen_label": f"G_ECC_ENC{label_suffix}",
        }

        if has_ecc:
            wp["pad_data_signal"] = f"{prefix}pad_wr_data"
            wp["ecc_wr_data"] = f"{prefix}ecc_wr_data"
            wp["phy_wr_data"] = f"{prefix}ecc_wr_data"
        else:
            wp["phy_wr_data"] = f"{prefix}init_wr_data" if has_init_mux else pipe_wdata

        return wp

    @staticmethod
    def _make_read_path(prefix: str, clk: str,
                        rd_en_signal: str, rd_addr_signal: str,
                        phy_rd_data: str,
                        out_rd_data_port: str,
                        ecc_port_prefix: str,
                        port_label: str = "") -> dict:
        """Build a read_path entry for the template."""
        bare = prefix.rstrip("_") if prefix else ""
        gv_suffix = f"_{bare}" if bare else ""
        label_suffix = f"_{bare.upper()}" if bare else ""

        return {
            "prefix": prefix,
            "prefix_bare": bare or "0",
            "port_label": port_label,
            "clk": clk,
            "rd_en_signal": rd_en_signal,
            "rd_addr_signal": rd_addr_signal,
            "phy_rd_data": phy_rd_data,
            "out_rd_data_port": out_rd_data_port,
            "out_ecc_correctable_valid": f"o_{ecc_port_prefix}ecc_correctable_valid",
            "out_ecc_correctable_addr": f"o_{ecc_port_prefix}ecc_correctable_addr",
            "out_ecc_uncorrectable_valid": f"o_{ecc_port_prefix}ecc_uncorrectable_valid",
            "out_ecc_uncorrectable_addr": f"o_{ecc_port_prefix}ecc_uncorrectable_addr",
            "out_ecc_syndrome": f"o_{ecc_port_prefix}ecc_err_syndrome",
            "genvar_dec": f"g_dec{gv_suffix}",
            "dec_gen_label": f"G_ECC_DEC{label_suffix}",
            "genvar_noec": f"g_noec{gv_suffix}",
            "noec_gen_label": f"G_NO_ECC{label_suffix}",
        }

    @staticmethod
    def _make_input_pipe(inst_name: str, clk: str,
                         signals: list[tuple[str, str, str]]) -> dict:
        """Build an input_pipe entry.

        signals: list of (input_name, width_expr, pipe_name).
        """
        wire_decls: list[str] = []
        data_in: list[str] = []
        data_out: list[str] = []
        width_parts: list[str] = []

        for input_name, width_expr, pipe_name in signals:
            if width_expr == "1":
                wire_decls.append(f"wire                  {pipe_name};")
                width_parts.append("1")
            else:
                wire_decls.append(f"wire [{width_expr}-1:0] {pipe_name};")
                width_parts.append(width_expr)
            data_in.append(input_name)
            data_out.append(pipe_name)

        return {
            "inst_name": inst_name,
            "clk": clk,
            "wire_decls": wire_decls,
            "total_width_expr": " + ".join(width_parts),
            "data_in": data_in,
            "data_out": data_out,
        }

    @staticmethod
    def _build_phy_bwen(total_mask: int, mask_width: int,
                        mask_per_slice: int, pipe_bwen: str,
                        init_guard: bool) -> str:
        """Build phy_bwen Verilog assignment.

        When mask_per_slice > 1, each user mask bit is replicated
        mask_per_slice times to cover the padded ECC codeword.
        """
        init_prefix = (f"init_ram_en"
                       f" ? {{{total_mask}{{1'b1}}}}"
                       f" : " if init_guard else "")

        if mask_per_slice > 1:
            parts: list[str] = []
            remaining = total_mask - mask_per_slice * mask_width
            if remaining > 0:
                parts.append(f"{{{remaining}{{1'b1}}}}")
            for i in range(mask_width - 1, -1, -1):
                parts.append(f"{{{mask_per_slice}{{{pipe_bwen}[{i}]}}}}")
            return f"{init_prefix}{{{', '.join(parts)}}};"
        else:
            pad = total_mask - mask_width
            if pad > 0:
                return f"{init_prefix}{{{{{pad}{{1'b1}}}}, {pipe_bwen}}};"
            else:
                return f"{init_prefix}{pipe_bwen};"


# ---------------------------------------------------------------------------
# SinglePortWrapperGen — 1rw, 1rwm
# ---------------------------------------------------------------------------

class SinglePortWrapperGen(MemoryWrapperGenerator):

    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      ecc_modules: EccModuleInfo | None,
                      interface_type: InterfaceType,
                      module_name: str, phy_wrapper_name: str,
                      tiling: TilingParams) -> dict:
        ctx = self._base_context(mem_spec, ecc_params, ecc_modules,
                                 interface_type, module_name, phy_wrapper_name,
                                 tiling)
        has_ecc = ctx["has_ecc"]
        has_mask = ctx["has_mask"]

        # -- Module ports --
        ports = [
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
        ports.append("// Memory port")
        ports.append("input                          i_cen")
        ports.append("input                          i_wen")
        ports.append("input  [ADDR_WIDTH-1:0]        i_addr")
        ports.append("input  [DATA_WIDTH-1:0]        i_wdata")
        if has_mask:
            mask_w = ctx["mask_width"]
            ports.append(f"input  [{mask_w}-1:0]        i_bwen")
        ports.append("output [DATA_WIDTH-1:0]        o_rdata")
        if has_ecc:
            ports.append("// ECC status")
            ports.append("output                         o_ecc_correctable_valid")
            ports.append("output [ADDR_WIDTH-1:0]        o_ecc_correctable_addr")
            ports.append("output                         o_ecc_uncorrectable_valid")
            ports.append("output [ADDR_WIDTH-1:0]        o_ecc_uncorrectable_addr")
            if ctx["detailed_report"]:
                ports.append(f"output [{ecc_params.m}-1:0]        o_ecc_err_syndrome")

        # -- Input pipeline --
        pipe_signals: list[tuple[str, str, str]] = [
            ("i_cen",   "1",          "pipe_cen"),
            ("i_wen",   "1",          "pipe_wen"),
            ("i_addr",  "ADDR_WIDTH", "pipe_addr"),
            ("i_wdata", "DATA_WIDTH", "pipe_wdata"),
        ]
        if has_mask:
            pipe_signals.append(("i_bwen", "MASK_WIDTH", "pipe_bwen"))

        input_pipes = [self._make_input_pipe("u_input_pipe", "i_clk", pipe_signals)]

        # -- Write path --
        write_paths = [self._make_write_path(
            prefix="",
            pipe_wdata="pipe_wdata",
            pipe_bwen="pipe_bwen" if has_mask else None,
            has_init_mux=True,
            has_ecc=has_ecc,
        )]

        # -- Physical wrapper connection --
        phy_wr_data = write_paths[0]["phy_wr_data"]

        phy_connect = [
            "wire phy_cen = pipe_cen | init_ram_en;",
            "wire phy_wen = init_ram_en ? 1'b1 : (pipe_cen & pipe_wen);",
            "wire [ADDR_WIDTH-1:0] phy_addr = init_ram_en ? init_ram_addr : pipe_addr;",
        ]
        if has_mask:
            bwen_rhs = self._build_phy_bwen(
                ctx["total_mask_width"], ctx["mask_width"],
                ctx["mask_per_slice"], "pipe_bwen", init_guard=True)
            phy_connect.append(
                f"wire [TOTAL_MASK_WIDTH-1:0] phy_bwen = {bwen_rhs}")

        phy_inst_ports = [
            ".clk   (i_clk)",
            ".cen   (phy_cen)",
            ".wen   (phy_wen)",
            ".addr  (phy_addr)",
            f".wdata ({phy_wr_data})",
        ]
        if has_mask:
            phy_inst_ports.append(".bwen  (phy_bwen)")
        phy_inst_ports.append(".rdata (phy_rd_data)")

        # -- Read path --
        # SinglePort read: cen & ~wen is a read
        read_paths = [self._make_read_path(
            prefix="",
            clk="i_clk",
            rd_en_signal="pipe_cen & ~pipe_wen",
            rd_addr_signal="phy_addr",
            phy_rd_data="phy_rd_data",
            out_rd_data_port="o_rdata",
            ecc_port_prefix="",
        )]

        ctx.update({
            "module_ports": ports,
            "input_pipes": input_pipes,
            "init_clk": "i_clk",
            "write_paths": write_paths,
            "phy_connect_lines": phy_connect,
            "phy_inst_ports": phy_inst_ports,
            "read_paths": read_paths,
        })
        return ctx


# ---------------------------------------------------------------------------
# DualPortWrapperGen — 1r1w, 1r1wm, 1r1wa, 1r1wma
# ---------------------------------------------------------------------------

class DualPortWrapperGen(MemoryWrapperGenerator):

    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      ecc_modules: EccModuleInfo | None,
                      interface_type: InterfaceType,
                      module_name: str, phy_wrapper_name: str,
                      tiling: TilingParams) -> dict:
        ctx = self._base_context(mem_spec, ecc_params, ecc_modules,
                                 interface_type, module_name, phy_wrapper_name,
                                 tiling)
        has_ecc = ctx["has_ecc"]
        has_mask = ctx["has_mask"]
        is_async = ctx["is_async"]

        wr_clk = "i_wr_clk" if is_async else "i_clk"
        rd_clk = "i_rd_clk" if is_async else "i_clk"

        # -- Module ports --
        ports = []
        if is_async:
            ports.append("input                          i_wr_clk")
            ports.append("input                          i_rd_clk")
        else:
            ports.append("input                          i_clk")
        ports.append("input                          i_rst_n")
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
            mask_w = ctx["mask_width"]
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
            if ctx["detailed_report"]:
                ports.append(f"output [{ecc_params.m}-1:0]        o_ecc_err_syndrome")

        # -- Input pipeline --
        wr_signals: list[tuple[str, str, str]] = [
            ("i_wr_en",   "1",          "pipe_wr_en"),
            ("i_wr_addr", "ADDR_WIDTH", "pipe_wr_addr"),
            ("i_wr_data", "DATA_WIDTH", "pipe_wr_data"),
        ]
        if has_mask:
            wr_signals.append(("i_wr_bwen", "MASK_WIDTH", "pipe_wr_bwen"))

        rd_signals: list[tuple[str, str, str]] = [
            ("i_rd_en",   "1",          "pipe_rd_en"),
            ("i_rd_addr", "ADDR_WIDTH", "pipe_rd_addr"),
        ]

        if is_async:
            input_pipes = [
                self._make_input_pipe("u_wr_input_pipe", wr_clk, wr_signals),
                self._make_input_pipe("u_rd_input_pipe", rd_clk, rd_signals),
            ]
        else:
            input_pipes = [
                self._make_input_pipe("u_input_pipe", "i_clk", wr_signals + rd_signals),
            ]

        # -- Write path --
        write_paths = [self._make_write_path(
            prefix="",
            pipe_wdata="pipe_wr_data",
            pipe_bwen="pipe_wr_bwen" if has_mask else None,
            has_init_mux=True,
            has_ecc=has_ecc,
        )]

        # -- Physical wrapper connection --
        phy_wr_data = write_paths[0]["phy_wr_data"]

        phy_connect = [
            "wire phy_wr_en = pipe_wr_en | init_ram_en;",
            "wire [ADDR_WIDTH-1:0] phy_wr_addr = init_ram_en ? init_ram_addr : pipe_wr_addr;",
        ]
        if has_mask:
            bwen_rhs = self._build_phy_bwen(
                ctx["total_mask_width"], ctx["mask_width"],
                ctx["mask_per_slice"], "pipe_wr_bwen", init_guard=True)
            phy_connect.append(
                f"wire [TOTAL_MASK_WIDTH-1:0] phy_wr_mask = {bwen_rhs}")

        phy_inst_ports: list[str] = []
        if is_async:
            phy_inst_ports.append(".wr_clk  (i_wr_clk)")
            phy_inst_ports.append(".rd_clk  (i_rd_clk)")
        else:
            phy_inst_ports.append(".clk     (i_clk)")
        phy_inst_ports.extend([
            ".wr_en   (phy_wr_en)",
            ".wr_addr (phy_wr_addr)",
            f".wr_data ({phy_wr_data})",
        ])
        if has_mask:
            phy_inst_ports.append(".wr_mask (phy_wr_mask)")
        phy_inst_ports.extend([
            ".rd_en   (pipe_rd_en)",
            ".rd_addr (pipe_rd_addr)",
            ".rd_data (phy_rd_data)",
        ])

        # -- Read path --
        read_paths = [self._make_read_path(
            prefix="",
            clk=rd_clk,
            rd_en_signal="pipe_rd_en",
            rd_addr_signal="pipe_rd_addr",
            phy_rd_data="phy_rd_data",
            out_rd_data_port="o_rd_data",
            ecc_port_prefix="",
        )]

        ctx.update({
            "module_ports": ports,
            "input_pipes": input_pipes,
            "init_clk": wr_clk,
            "write_paths": write_paths,
            "phy_connect_lines": phy_connect,
            "phy_inst_ports": phy_inst_ports,
            "read_paths": read_paths,
        })
        return ctx


# ---------------------------------------------------------------------------
# TrueDualPortWrapperGen — 2rw, 2rwm
# ---------------------------------------------------------------------------

class TrueDualPortWrapperGen(MemoryWrapperGenerator):

    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      ecc_modules: EccModuleInfo | None,
                      interface_type: InterfaceType,
                      module_name: str, phy_wrapper_name: str,
                      tiling: TilingParams) -> dict:
        ctx = self._base_context(mem_spec, ecc_params, ecc_modules,
                                 interface_type, module_name, phy_wrapper_name,
                                 tiling)
        has_ecc = ctx["has_ecc"]
        has_mask = ctx["has_mask"]

        # -- Module ports --
        ports = [
            "input                          i_a_clk",
            "input                          i_b_clk",
            "input                          i_rst_n",
        ]
        if has_ecc:
            ports.append("// ECC control")
            ports.append("input                          i_ecc_en")
            ports.append("input  [1:0]                   i_ecc_err_insert")
            ports.append("input  [1:0]                   i_ecc_err_mask")
        ports.append("// Init (via Port A)")
        ports.append("input                          i_init_en")
        ports.append("input                          i_init_value")
        ports.append("output                         o_init_done")
        # Port A
        ports.append("// Port A")
        ports.append("input                          i_a_cen")
        ports.append("input                          i_a_wen")
        ports.append("input  [ADDR_WIDTH-1:0]        i_a_addr")
        ports.append("input  [DATA_WIDTH-1:0]        i_a_wdata")
        if has_mask:
            mask_w = ctx["mask_width"]
            ports.append(f"input  [{mask_w}-1:0]        i_a_bwen")
        ports.append("output [DATA_WIDTH-1:0]        o_a_rdata")
        # Port B
        ports.append("// Port B")
        ports.append("input                          i_b_cen")
        ports.append("input                          i_b_wen")
        ports.append("input  [ADDR_WIDTH-1:0]        i_b_addr")
        ports.append("input  [DATA_WIDTH-1:0]        i_b_wdata")
        if has_mask:
            ports.append(f"input  [{mask_w}-1:0]        i_b_bwen")
        ports.append("output [DATA_WIDTH-1:0]        o_b_rdata")
        if has_ecc:
            ports.append("// ECC status (Port A)")
            ports.append("output                         o_a_ecc_correctable_valid")
            ports.append("output [ADDR_WIDTH-1:0]        o_a_ecc_correctable_addr")
            ports.append("output                         o_a_ecc_uncorrectable_valid")
            ports.append("output [ADDR_WIDTH-1:0]        o_a_ecc_uncorrectable_addr")
            if ctx["detailed_report"]:
                ports.append(f"output [{ecc_params.m}-1:0]        o_a_ecc_err_syndrome")
            ports.append("// ECC status (Port B)")
            ports.append("output                         o_b_ecc_correctable_valid")
            ports.append("output [ADDR_WIDTH-1:0]        o_b_ecc_correctable_addr")
            ports.append("output                         o_b_ecc_uncorrectable_valid")
            ports.append("output [ADDR_WIDTH-1:0]        o_b_ecc_uncorrectable_addr")
            if ctx["detailed_report"]:
                ports.append(f"output [{ecc_params.m}-1:0]        o_b_ecc_err_syndrome")

        # -- Input pipelines (A and B on separate clocks) --
        a_signals: list[tuple[str, str, str]] = [
            ("i_a_cen",   "1",          "pipe_a_cen"),
            ("i_a_wen",   "1",          "pipe_a_wen"),
            ("i_a_addr",  "ADDR_WIDTH", "pipe_a_addr"),
            ("i_a_wdata", "DATA_WIDTH", "pipe_a_wdata"),
        ]
        if has_mask:
            a_signals.append(("i_a_bwen", "MASK_WIDTH", "pipe_a_bwen"))

        b_signals: list[tuple[str, str, str]] = [
            ("i_b_cen",   "1",          "pipe_b_cen"),
            ("i_b_wen",   "1",          "pipe_b_wen"),
            ("i_b_addr",  "ADDR_WIDTH", "pipe_b_addr"),
            ("i_b_wdata", "DATA_WIDTH", "pipe_b_wdata"),
        ]
        if has_mask:
            b_signals.append(("i_b_bwen", "MASK_WIDTH", "pipe_b_bwen"))

        input_pipes = [
            self._make_input_pipe("u_a_input_pipe", "i_a_clk", a_signals),
            self._make_input_pipe("u_b_input_pipe", "i_b_clk", b_signals),
        ]

        # -- Write paths (A with init, B without) --
        write_paths = [
            self._make_write_path(
                prefix="a_",
                pipe_wdata="pipe_a_wdata",
                pipe_bwen="pipe_a_bwen" if has_mask else None,
                has_init_mux=True,
                has_ecc=has_ecc,
            ),
            self._make_write_path(
                prefix="b_",
                pipe_wdata="pipe_b_wdata",
                pipe_bwen="pipe_b_bwen" if has_mask else None,
                has_init_mux=False,
                has_ecc=has_ecc,
            ),
        ]

        # -- Physical wrapper connection --
        a_wr_data = write_paths[0]["phy_wr_data"]
        b_wr_data = write_paths[1]["phy_wr_data"]

        phy_connect = [
            "// Port A (with init)",
            "wire phy_a_cen = pipe_a_cen | init_ram_en;",
            "wire phy_a_wen = init_ram_en ? 1'b1 : (pipe_a_cen & pipe_a_wen);",
            "wire [ADDR_WIDTH-1:0] phy_a_addr = init_ram_en ? init_ram_addr : pipe_a_addr;",
        ]
        if has_mask:
            a_bwen_rhs = self._build_phy_bwen(
                ctx["total_mask_width"], ctx["mask_width"],
                ctx["mask_per_slice"], "pipe_a_bwen", init_guard=True)
            phy_connect.append(
                f"wire [TOTAL_MASK_WIDTH-1:0] phy_a_bwen = {a_bwen_rhs}")

        phy_connect.extend([
            "// Port B",
            "wire phy_b_cen = pipe_b_cen;",
            "wire phy_b_wen = pipe_b_cen & pipe_b_wen;",
            "wire [ADDR_WIDTH-1:0] phy_b_addr = pipe_b_addr;",
        ])
        if has_mask:
            b_bwen_rhs = self._build_phy_bwen(
                ctx["total_mask_width"], ctx["mask_width"],
                ctx["mask_per_slice"], "pipe_b_bwen", init_guard=False)
            phy_connect.append(
                f"wire [TOTAL_MASK_WIDTH-1:0] phy_b_bwen = {b_bwen_rhs}")

        phy_inst_ports = [
            ".a_clk   (i_a_clk)",
            ".a_cen   (phy_a_cen)",
            ".a_wen   (phy_a_wen)",
            ".a_addr  (phy_a_addr)",
            f".a_wdata ({a_wr_data})",
        ]
        if has_mask:
            phy_inst_ports.append(".a_bwen  (phy_a_bwen)")
        phy_inst_ports.append(".a_rdata (phy_a_rd_data)")

        phy_inst_ports.extend([
            ".b_clk   (i_b_clk)",
            ".b_cen   (phy_b_cen)",
            ".b_wen   (phy_b_wen)",
            ".b_addr  (phy_b_addr)",
            f".b_wdata ({b_wr_data})",
        ])
        if has_mask:
            phy_inst_ports.append(".b_bwen  (phy_b_bwen)")
        phy_inst_ports.append(".b_rdata (phy_b_rd_data)")

        # -- Read paths (A and B independent) --
        read_paths = [
            self._make_read_path(
                prefix="a_", clk="i_a_clk",
                rd_en_signal="pipe_a_cen & ~pipe_a_wen",
                rd_addr_signal="phy_a_addr",
                phy_rd_data="phy_a_rd_data",
                out_rd_data_port="o_a_rdata",
                ecc_port_prefix="a_",
                port_label="Port A",
            ),
            self._make_read_path(
                prefix="b_", clk="i_b_clk",
                rd_en_signal="pipe_b_cen & ~pipe_b_wen",
                rd_addr_signal="phy_b_addr",
                phy_rd_data="phy_b_rd_data",
                out_rd_data_port="o_b_rdata",
                ecc_port_prefix="b_",
                port_label="Port B",
            ),
        ]

        ctx.update({
            "module_ports": ports,
            "input_pipes": input_pipes,
            "init_clk": "i_a_clk",
            "write_paths": write_paths,
            "phy_connect_lines": phy_connect,
            "phy_inst_ports": phy_inst_ports,
            "read_paths": read_paths,
        })
        return ctx


# ---------------------------------------------------------------------------
# RomWrapperGen — rom
# ---------------------------------------------------------------------------

class RomWrapperGen(MemoryWrapperGenerator):

    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      ecc_modules: EccModuleInfo | None,
                      interface_type: InterfaceType,
                      module_name: str, phy_wrapper_name: str,
                      tiling: TilingParams) -> dict:
        ctx = self._base_context(mem_spec, ecc_params, ecc_modules,
                                 interface_type, module_name, phy_wrapper_name,
                                 tiling)
        has_ecc = ctx["has_ecc"]

        # -- Module ports --
        ports = [
            "input                          i_clk",
            "input                          i_rst_n",
        ]
        if has_ecc:
            ports.append("// ECC control")
            ports.append("input                          i_ecc_en")
            ports.append("input  [1:0]                   i_ecc_err_insert")
            ports.append("input  [1:0]                   i_ecc_err_mask")
        ports.append("// Memory port")
        ports.append("input                          i_cen")
        ports.append("input  [ADDR_WIDTH-1:0]        i_addr")
        ports.append("output [DATA_WIDTH-1:0]        o_rdata")
        if has_ecc:
            ports.append("// ECC status")
            ports.append("output                         o_ecc_correctable_valid")
            ports.append("output [ADDR_WIDTH-1:0]        o_ecc_correctable_addr")
            ports.append("output                         o_ecc_uncorrectable_valid")
            ports.append("output [ADDR_WIDTH-1:0]        o_ecc_uncorrectable_addr")
            if ctx["detailed_report"]:
                ports.append(f"output [{ecc_params.m}-1:0]        o_ecc_err_syndrome")

        # -- Input pipeline --
        pipe_signals: list[tuple[str, str, str]] = [
            ("i_cen",  "1",          "pipe_cen"),
            ("i_addr", "ADDR_WIDTH", "pipe_addr"),
        ]
        input_pipes = [self._make_input_pipe("u_input_pipe", "i_clk", pipe_signals)]

        # -- No write paths, no init --
        write_paths: list[dict] = []

        # -- Physical wrapper connection --
        phy_inst_ports = [
            ".clk   (i_clk)",
            ".cen   (pipe_cen)",
            ".addr  (pipe_addr)",
            ".rdata (phy_rd_data)",
        ]

        # -- Read path --
        read_paths = [self._make_read_path(
            prefix="",
            clk="i_clk",
            rd_en_signal="pipe_cen",
            rd_addr_signal="pipe_addr",
            phy_rd_data="phy_rd_data",
            out_rd_data_port="o_rdata",
            ecc_port_prefix="",
        )]

        ctx.update({
            "module_ports": ports,
            "input_pipes": input_pipes,
            "write_paths": write_paths,
            "phy_connect_lines": [],
            "phy_inst_ports": phy_inst_ports,
            "read_paths": read_paths,
        })
        return ctx


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

GENERATORS: dict[str, MemoryWrapperGenerator] = {
    "single_port":    SinglePortWrapperGen(),
    "dual_port":      DualPortWrapperGen(),
    "true_dual_port": TrueDualPortWrapperGen(),
    "rom":            RomWrapperGen(),
}


def gen_memory_wrapper(mem_spec: MemorySpec, ecc_params: EccParams,
                       ecc_modules: EccModuleInfo | None,
                       interface_type: InterfaceType,
                       module_name: str, phy_wrapper_name: str,
                       tiling: TilingParams) -> str:
    """Entry point — dispatch to the correct generator by base_type."""
    generator = GENERATORS[interface_type.base_type]
    return generator.generate(mem_spec, ecc_params, ecc_modules,
                              interface_type, module_name, phy_wrapper_name,
                              tiling)
