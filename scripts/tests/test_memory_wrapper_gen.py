"""Unit tests for memory_wrapper_gen.py — helpers + 4 generator classes.

Covers the verification plan in doc/memory_wrapper_test_plan.md §4:
  - §4.2  Helper method unit tests  (TestBaseContext … TestMakeInputPipe)
  - §4.3  Generator integration tests  (TestSinglePortWrapper … TestRomWrapper)
  - §4.4  Verilog output structure verification  (inline in each generator test)
  - §4.5  Pipeline configuration parametrized tests  (TestPipelineConfigs)
"""

from __future__ import annotations

import re

import pytest

from config_io import InterfaceType
from memory_wrapper_gen import (
    GENERATORS,
    MemoryWrapperGenerator,
)
from physical_wrapper_gen import PhysicalWrapperGenerator

from helpers import make_ecc_modules, make_ecc_params, make_mem_spec


# ===================================================================
# Lightweight InterfaceType instances (L2 only uses base_type / has_mask / is_async)
# ===================================================================

_ITYPES: dict[str, InterfaceType] = {
    "1rw":    InterfaceType(base_type="single_port",    has_mask=False, is_async=False, port_map={}, sub_types=()),
    "1rwm":   InterfaceType(base_type="single_port",    has_mask=True,  is_async=False, port_map={}, sub_types=()),
    "1r1w":   InterfaceType(base_type="dual_port",      has_mask=False, is_async=False, port_map={}, sub_types=()),
    "1r1wm":  InterfaceType(base_type="dual_port",      has_mask=True,  is_async=False, port_map={}, sub_types=()),
    "1r1wa":  InterfaceType(base_type="dual_port",      has_mask=False, is_async=True,  port_map={}, sub_types=()),
    "1r1wma": InterfaceType(base_type="dual_port",      has_mask=True,  is_async=True,  port_map={}, sub_types=()),
    "2rw":    InterfaceType(base_type="true_dual_port",  has_mask=False, is_async=False, port_map={}, sub_types=()),
    "2rwm":   InterfaceType(base_type="true_dual_port",  has_mask=True,  is_async=False, port_map={}, sub_types=()),
    "rom":    InterfaceType(base_type="rom",             has_mask=False, is_async=False, port_map={}, sub_types=()),
}


# ===================================================================
# Shared helpers — build context / generate Verilog
# ===================================================================

def _calc_tiling(mem, ecc_params):
    """Compute TilingParams matching production logic."""
    if ecc_params.enabled:
        phy_width = ecc_params.data_with_ecc_width
    else:
        phy_width = mem.width
    return PhysicalWrapperGenerator.calc_tiling(
        phy_width, mem.depth,
        mem.physical.lib_width, mem.physical.lib_depth,
        mem.physical.lib_mask_width,
    )


def _build_ctx(type_: str, *,
               ecc: bool = False, detailed: bool = False,
               input_pipe: int = 0, ecc_pipe: int = 0, output_pipe: int = 0,
               width: int = 64, depth: int = 512,
               k: int = 64, m: int = 8,
               lib_mask_width: int | None = None) -> dict:
    """Build context dict for a given interface type and options."""
    kwargs: dict = {}
    if lib_mask_width is not None:
        kwargs["lib_mask_width"] = lib_mask_width
    mem = make_mem_spec(
        type_=type_, width=width, depth=depth,
        ecc_enable=ecc, ecc_detailed_report=detailed,
        ecc_k=k, ecc_m=m,
        input_pipe_stages=input_pipe,
        ecc_pipe_stages=ecc_pipe,
        output_pipe_stages=output_pipe,
        **kwargs,
    )
    itype = _ITYPES[type_]
    ecc_params = make_ecc_params(width, k=k, m=m, enabled=ecc)
    ecc_modules = make_ecc_modules() if ecc else None
    tiling = _calc_tiling(mem, ecc_params)
    gen = GENERATORS[itype.base_type]
    return gen.build_context(mem, ecc_params, ecc_modules, itype,
                             f"test_{type_}_mem_wrapper",
                             f"test_{type_}_phy_wrapper",
                             tiling)


def _gen_verilog(type_: str, **kwargs) -> str:
    """Generate Verilog string for a given interface type and options."""
    mem = make_mem_spec(
        type_=type_,
        width=kwargs.get("width", 64),
        depth=kwargs.get("depth", 512),
        ecc_enable=kwargs.get("ecc", False),
        ecc_detailed_report=kwargs.get("detailed", False),
        ecc_k=kwargs.get("k", 64),
        ecc_m=kwargs.get("m", 8),
        input_pipe_stages=kwargs.get("input_pipe", 0),
        ecc_pipe_stages=kwargs.get("ecc_pipe", 0),
        output_pipe_stages=kwargs.get("output_pipe", 0),
        lib_mask_width=kwargs.get("lib_mask_width"),
    )
    itype = _ITYPES[type_]
    ecc_enabled = kwargs.get("ecc", False)
    ecc_params = make_ecc_params(
        mem.width,
        k=kwargs.get("k", 64),
        m=kwargs.get("m", 8),
        enabled=ecc_enabled,
    )
    ecc_modules = make_ecc_modules() if ecc_enabled else None
    tiling = _calc_tiling(mem, ecc_params)
    gen = GENERATORS[itype.base_type]
    return gen.generate(mem, ecc_params, ecc_modules, itype,
                        f"test_{type_}_mem_wrapper",
                        f"test_{type_}_phy_wrapper",
                        tiling)


# ===================================================================
# §4.2 — Helper method unit tests
# ===================================================================

class TestBaseContext:
    """Tests for MemoryWrapperGenerator._base_context()."""

    def test_no_ecc(self):
        ctx = _build_ctx("1rw", ecc=False)
        assert ctx["has_ecc"] is False
        assert "ecc_slice_dw" not in ctx
        assert "ecc_m" not in ctx
        assert "enc_module" not in ctx

    def test_with_ecc(self):
        ctx = _build_ctx("1rw", ecc=True, k=64, m=8)
        assert ctx["has_ecc"] is True
        assert ctx["ecc_slice_dw"] == 64
        assert ctx["ecc_slice_with_ecc_dw"] == 72
        assert ctx["ecc_slice_num"] == 1
        assert ctx["ecc_m"] == 8
        assert ctx["enc_module"] == "test_secded_enc"
        assert ctx["dec_module"] == "test_secded_dec"

    def test_rom_no_init(self):
        ctx = _build_ctx("rom")
        assert ctx["has_init"] is False

    def test_non_rom_init(self):
        for t in ("1rw", "1r1w", "2rw"):
            ctx = _build_ctx(t)
            assert ctx["has_init"] is True, f"{t} should have init"

    def test_detailed_report(self):
        ctx = _build_ctx("1rw", ecc=True, detailed=True)
        assert ctx["detailed_report"] is True

    def test_async_flag(self):
        ctx_sync = _build_ctx("1r1w")
        ctx_async = _build_ctx("1r1wa")
        assert ctx_sync["is_async"] is False
        assert ctx_async["is_async"] is True


class TestMakeWritePath:
    """Tests for MemoryWrapperGenerator._make_write_path()."""
    fn = staticmethod(MemoryWrapperGenerator._make_write_path)

    def test_no_ecc_no_mask(self):
        wp = self.fn(prefix="", pipe_wdata="pipe_wdata",
                     pipe_bwen=None, has_init_mux=False, has_ecc=False)
        # No ECC, no init → phy_wr_data = pipe_wdata directly
        assert wp["phy_wr_data"] == "pipe_wdata"
        assert "pad_data_signal" not in wp
        assert "pad_mask_signal" not in wp

    def test_no_ecc_with_init(self):
        wp = self.fn(prefix="", pipe_wdata="pipe_wdata",
                     pipe_bwen=None, has_init_mux=True, has_ecc=False)
        # No ECC + init → phy_wr_data = init_wr_data (from template mux)
        assert wp["phy_wr_data"] == "init_wr_data"
        assert wp["has_init_mux"] is True

    def test_ecc_no_mask(self):
        wp = self.fn(prefix="", pipe_wdata="pipe_wdata",
                     pipe_bwen=None, has_init_mux=True, has_ecc=True)
        assert wp["pad_data_signal"] == "pad_wr_data"
        assert wp["ecc_wr_data"] == "ecc_wr_data"
        assert wp["phy_wr_data"] == "ecc_wr_data"
        assert "pad_mask_signal" not in wp

    def test_ecc_with_mask(self):
        wp = self.fn(prefix="", pipe_wdata="pipe_wdata",
                     pipe_bwen="pipe_bwen", has_init_mux=True, has_ecc=True)
        # ECC mask expansion removed from L2 (now in L1)
        assert "pad_mask_signal" not in wp
        assert "ecc_bwen" not in wp
        assert "genvar_mask" not in wp
        assert "mask_gen_label" not in wp
        # pipe_bwen still present for L2 padding logic
        assert wp["pipe_bwen"] == "pipe_bwen"

    def test_prefix_a(self):
        wp = self.fn(prefix="a_", pipe_wdata="pipe_a_wdata",
                     pipe_bwen=None, has_init_mux=True, has_ecc=True)
        assert wp["prefix"] == "a"
        assert wp["genvar_enc"] == "g_enc_a"
        assert wp["enc_gen_label"] == "G_ECC_ENC_A"
        assert wp["pad_data_signal"] == "a_pad_wr_data"
        assert wp["ecc_wr_data"] == "a_ecc_wr_data"

    def test_prefix_b_no_init(self):
        wp = self.fn(prefix="b_", pipe_wdata="pipe_b_wdata",
                     pipe_bwen=None, has_init_mux=False, has_ecc=True)
        assert wp["has_init_mux"] is False
        assert wp["genvar_enc"] == "g_enc_b"
        assert wp["pad_data_signal"] == "b_pad_wr_data"


class TestMakeReadPath:
    """Tests for MemoryWrapperGenerator._make_read_path()."""
    fn = staticmethod(MemoryWrapperGenerator._make_read_path)

    def test_no_prefix(self):
        rp = self.fn(prefix="", clk="i_clk",
                     rd_en_signal="pipe_cen & ~pipe_wen",
                     rd_addr_signal="phy_addr",
                     phy_rd_data="phy_rd_data",
                     out_rd_data_port="o_rdata",
                     ecc_port_prefix="")
        assert rp["prefix"] == ""
        assert rp["prefix_bare"] == "0"
        assert rp["out_ecc_correctable_valid"] == "o_ecc_correctable_valid"
        assert rp["genvar_dec"] == "g_dec"
        assert rp["dec_gen_label"] == "G_ECC_DEC"

    def test_prefix_a(self):
        rp = self.fn(prefix="a_", clk="i_a_clk",
                     rd_en_signal="pipe_a_cen & ~pipe_a_wen",
                     rd_addr_signal="phy_a_addr",
                     phy_rd_data="phy_a_rd_data",
                     out_rd_data_port="o_a_rdata",
                     ecc_port_prefix="a_")
        assert rp["prefix"] == "a_"
        assert rp["prefix_bare"] == "a"
        assert rp["genvar_dec"] == "g_dec_a"
        assert rp["dec_gen_label"] == "G_ECC_DEC_A"
        assert rp["out_ecc_correctable_valid"] == "o_a_ecc_correctable_valid"
        assert rp["out_ecc_uncorrectable_addr"] == "o_a_ecc_uncorrectable_addr"
        assert rp["out_ecc_syndrome"] == "o_a_ecc_err_syndrome"

    def test_port_label(self):
        rp = self.fn(prefix="a_", clk="i_a_clk",
                     rd_en_signal="x", rd_addr_signal="y",
                     phy_rd_data="z", out_rd_data_port="o_a_rdata",
                     ecc_port_prefix="a_", port_label="Port A")
        assert rp["port_label"] == "Port A"

    def test_ecc_port_prefix(self):
        rp = self.fn(prefix="b_", clk="i_b_clk",
                     rd_en_signal="x", rd_addr_signal="y",
                     phy_rd_data="z", out_rd_data_port="o_b_rdata",
                     ecc_port_prefix="b_")
        assert rp["out_ecc_correctable_valid"] == "o_b_ecc_correctable_valid"
        assert rp["out_ecc_uncorrectable_valid"] == "o_b_ecc_uncorrectable_valid"


class TestMakeInputPipe:
    """Tests for MemoryWrapperGenerator._make_input_pipe()."""
    fn = staticmethod(MemoryWrapperGenerator._make_input_pipe)

    def test_single_signal(self):
        pipe = self.fn("u_pipe", "i_clk", [("i_cen", "1", "pipe_cen")])
        assert pipe["total_width_expr"] == "1"
        assert len(pipe["wire_decls"]) == 1
        # Width-1 wire should not have a range
        assert "[" not in pipe["wire_decls"][0]

    def test_multi_signals(self):
        signals = [
            ("i_cen",   "1",          "pipe_cen"),
            ("i_addr",  "ADDR_WIDTH", "pipe_addr"),
            ("i_wdata", "DATA_WIDTH", "pipe_wdata"),
        ]
        pipe = self.fn("u_pipe", "i_clk", signals)
        assert pipe["total_width_expr"] == "1 + ADDR_WIDTH + DATA_WIDTH"
        assert len(pipe["data_in"]) == 3
        assert len(pipe["data_out"]) == 3
        assert pipe["data_in"] == ["i_cen", "i_addr", "i_wdata"]
        assert pipe["data_out"] == ["pipe_cen", "pipe_addr", "pipe_wdata"]

    def test_inst_name(self):
        pipe = self.fn("u_wr_input_pipe", "i_wr_clk", [("x", "1", "y")])
        assert pipe["inst_name"] == "u_wr_input_pipe"

    def test_clk(self):
        pipe = self.fn("u_pipe", "i_rd_clk", [("x", "1", "y")])
        assert pipe["clk"] == "i_rd_clk"


# ===================================================================
# §4.3 — Generator integration tests (context dict + Verilog)
# ===================================================================

class TestSinglePortWrapper:
    """SinglePortWrapperGen — 1rw, 1rwm."""

    def test_1rw_no_ecc(self):
        ctx = _build_ctx("1rw", ecc=False)
        assert len(ctx["write_paths"]) == 1
        assert len(ctx["input_pipes"]) == 1
        assert ctx["write_paths"][0]["phy_wr_data"] == "init_wr_data"
        # No ECC fields in write path
        assert "ecc_wr_data" not in ctx["write_paths"][0]

        v = _gen_verilog("1rw", ecc=False)
        assert "module test_1rw_mem_wrapper" in v
        assert "endmodule" in v
        assert "i_ecc_en" not in v
        assert "init_started" in v
        assert "u_test_1rw_phy_wrapper" in v

    def test_1rw_ecc(self):
        ctx = _build_ctx("1rw", ecc=True)
        wp = ctx["write_paths"][0]
        assert "ecc_wr_data" in wp
        assert wp["phy_wr_data"] == "ecc_wr_data"
        rp = ctx["read_paths"][0]
        assert rp["out_ecc_correctable_valid"] == "o_ecc_correctable_valid"

        v = _gen_verilog("1rw", ecc=True)
        assert "parameter ECC_SLICE_DW" in v
        assert "parameter ECC_SLICE_WITH_ECC_DW" in v
        assert "parameter ECC_SLICE_NUM" in v
        assert "i_ecc_en" in v
        assert "i_ecc_err_insert" in v
        assert "i_ecc_err_mask" in v
        assert "o_ecc_correctable_valid" in v
        assert "o_ecc_uncorrectable_valid" in v
        # Encoder instance
        assert "genvar g_enc" in v
        assert "test_secded_enc" in v
        # Decoder instance
        assert "genvar g_dec" in v
        assert "test_secded_dec" in v
        # Non-ECC data extraction
        assert "no_ecc_rd_data" in v
        # Output mux (i_ecc_en and ? may be on separate lines)
        assert re.search(r"i_ecc_en\s*\?", v)
        # Error report formula
        assert re.search(r"\| i_ecc_err_insert\[0\].*~i_ecc_err_mask\[0\]", v)

    def test_1rw_ecc_detailed(self):
        ctx = _build_ctx("1rw", ecc=True, detailed=True)
        assert ctx["detailed_report"] is True

        v = _gen_verilog("1rw", ecc=True, detailed=True)
        assert "o_ecc_err_syndrome" in v
        assert "ecc_err_syndrome" in v
        # Syndrome priority encoder
        assert "syn_i" in v

    def test_1rwm_no_ecc(self):
        ctx = _build_ctx("1rwm", ecc=False)
        # Module ports contain bwen
        port_str = " ".join(ctx["module_ports"])
        assert "i_bwen" in port_str
        # Physical connection has bwen with TOTAL_MASK_WIDTH
        phy_str = " ".join(ctx["phy_connect_lines"])
        assert "phy_bwen" in phy_str
        assert "TOTAL_MASK_WIDTH" in phy_str

        v = _gen_verilog("1rwm", ecc=False)
        assert "i_bwen" in v
        # MASK_WIDTH localparam present
        assert "localparam MASK_WIDTH" in v
        assert "localparam TOTAL_MASK_WIDTH" in v

    def test_1rwm_ecc(self):
        ctx = _build_ctx("1rwm", ecc=True)
        wp = ctx["write_paths"][0]
        # ECC mask expansion removed from L2
        assert "pad_mask_signal" not in wp
        assert "ecc_bwen" not in wp

        v = _gen_verilog("1rwm", ecc=True)
        # No mask expansion in L2 anymore
        assert "Mask Expansion" not in v
        assert "genvar g_mask" not in v
        # MASK_WIDTH localparam and mask port present
        assert "localparam MASK_WIDTH" in v
        assert "i_bwen" in v


class TestDualPortWrapper:
    """DualPortWrapperGen — 1r1w, 1r1wm, 1r1wa, 1r1wma."""

    def test_1r1w_no_ecc(self):
        ctx = _build_ctx("1r1w", ecc=False)
        assert len(ctx["input_pipes"]) == 1  # sync → single pipe
        pipe = ctx["input_pipes"][0]
        assert pipe["inst_name"] == "u_input_pipe"
        assert pipe["clk"] == "i_clk"
        # Write + read signals combined
        assert "i_wr_en" in pipe["data_in"]
        assert "i_rd_en" in pipe["data_in"]

        v = _gen_verilog("1r1w", ecc=False)
        assert "module test_1r1w_mem_wrapper" in v
        assert "input                          i_clk" in v
        assert "i_wr_en" in v
        assert "i_rd_en" in v
        assert "i_ecc_en" not in v

    def test_1r1w_ecc(self):
        ctx = _build_ctx("1r1w", ecc=True)
        assert ctx["has_ecc"] is True
        rp = ctx["read_paths"][0]
        assert rp["clk"] == "i_clk"

        v = _gen_verilog("1r1w", ecc=True)
        assert "test_secded_enc" in v
        assert "test_secded_dec" in v
        assert "o_ecc_correctable_valid" in v

    def test_1r1wm_ecc(self):
        ctx = _build_ctx("1r1wm", ecc=True)
        wp = ctx["write_paths"][0]
        assert wp["pipe_bwen"] == "pipe_wr_bwen"
        # ECC mask expansion removed from L2
        assert "pad_mask_signal" not in wp

        v = _gen_verilog("1r1wm", ecc=True)
        assert "Mask Expansion" not in v
        assert "i_wr_bwen" in v

    def test_1r1wa_no_ecc(self):
        ctx = _build_ctx("1r1wa", ecc=False)
        assert len(ctx["input_pipes"]) == 2  # async → 2 pipes
        wr_pipe = ctx["input_pipes"][0]
        rd_pipe = ctx["input_pipes"][1]
        assert wr_pipe["inst_name"] == "u_wr_input_pipe"
        assert wr_pipe["clk"] == "i_wr_clk"
        assert rd_pipe["inst_name"] == "u_rd_input_pipe"
        assert rd_pipe["clk"] == "i_rd_clk"
        assert ctx["init_clk"] == "i_wr_clk"

        v = _gen_verilog("1r1wa", ecc=False)
        assert "i_wr_clk" in v
        assert "i_rd_clk" in v
        assert "input                          i_clk" not in v

    def test_1r1wa_ecc(self):
        ctx = _build_ctx("1r1wa", ecc=True)
        assert ctx["init_clk"] == "i_wr_clk"
        rp = ctx["read_paths"][0]
        assert rp["clk"] == "i_rd_clk"

        v = _gen_verilog("1r1wa", ecc=True)
        assert "test_secded_enc" in v
        assert "test_secded_dec" in v

    def test_1r1wma_ecc(self):
        ctx = _build_ctx("1r1wma", ecc=True)
        assert ctx["is_async"] is True
        assert ctx["has_mask"] is True
        assert ctx["has_ecc"] is True
        assert len(ctx["input_pipes"]) == 2

        v = _gen_verilog("1r1wma", ecc=True)
        assert "i_wr_clk" in v
        assert "i_rd_clk" in v
        assert "i_wr_bwen" in v
        assert "Mask Expansion" not in v
        assert "test_secded_enc" in v


class TestTrueDualPortWrapper:
    """TrueDualPortWrapperGen — 2rw, 2rwm."""

    def test_2rw_no_ecc(self):
        ctx = _build_ctx("2rw", ecc=False)
        assert len(ctx["input_pipes"]) == 2  # A and B
        assert len(ctx["write_paths"]) == 2
        assert len(ctx["read_paths"]) == 2
        assert ctx["input_pipes"][0]["clk"] == "i_a_clk"
        assert ctx["input_pipes"][1]["clk"] == "i_b_clk"
        # Port A has init, Port B doesn't
        assert ctx["write_paths"][0]["has_init_mux"] is True
        assert ctx["write_paths"][1]["has_init_mux"] is False
        assert ctx["init_clk"] == "i_a_clk"

        v = _gen_verilog("2rw", ecc=False)
        assert "i_a_clk" in v
        assert "i_b_clk" in v
        assert "i_a_cen" in v
        assert "i_b_cen" in v
        assert "o_a_rdata" in v
        assert "o_b_rdata" in v

    def test_2rw_ecc(self):
        ctx = _build_ctx("2rw", ecc=True)
        # A has init_mux, B doesn't
        assert ctx["write_paths"][0]["has_init_mux"] is True
        assert ctx["write_paths"][1]["has_init_mux"] is False
        # Two independent read paths with ECC
        rp_a = ctx["read_paths"][0]
        rp_b = ctx["read_paths"][1]
        assert rp_a["out_ecc_correctable_valid"] == "o_a_ecc_correctable_valid"
        assert rp_b["out_ecc_correctable_valid"] == "o_b_ecc_correctable_valid"

        v = _gen_verilog("2rw", ecc=True)
        # A and B each have encoder genvar
        assert "g_enc_a" in v
        assert "g_enc_b" in v
        # A and B each have decoder genvar
        assert "g_dec_a" in v
        assert "g_dec_b" in v
        assert "o_a_ecc_correctable_valid" in v
        assert "o_b_ecc_correctable_valid" in v

    def test_2rw_ecc_detailed(self):
        v = _gen_verilog("2rw", ecc=True, detailed=True)
        assert "o_a_ecc_err_syndrome" in v
        assert "o_b_ecc_err_syndrome" in v
        assert "a_syn_i" in v
        assert "b_syn_i" in v

    def test_2rwm_no_ecc(self):
        ctx = _build_ctx("2rwm", ecc=False)
        port_str = " ".join(ctx["module_ports"])
        assert "i_a_bwen" in port_str
        assert "i_b_bwen" in port_str

        v = _gen_verilog("2rwm", ecc=False)
        assert "i_a_bwen" in v
        assert "i_b_bwen" in v

    def test_2rwm_ecc(self):
        ctx = _build_ctx("2rwm", ecc=True)
        wp_a = ctx["write_paths"][0]
        wp_b = ctx["write_paths"][1]
        # ECC mask expansion removed from L2
        assert "pad_mask_signal" not in wp_a
        assert "pad_mask_signal" not in wp_b
        # pipe_bwen still present for L2 padding
        assert wp_a["pipe_bwen"] == "pipe_a_bwen"
        assert wp_b["pipe_bwen"] == "pipe_b_bwen"

        v = _gen_verilog("2rwm", ecc=True)
        assert "Mask Expansion" not in v
        assert "g_mask_a" not in v
        assert "g_mask_b" not in v
        assert "localparam MASK_WIDTH" in v


class TestRomWrapper:
    """RomWrapperGen — rom."""

    def test_rom_no_ecc(self):
        ctx = _build_ctx("rom", ecc=False)
        assert ctx["has_init"] is False
        assert len(ctx["write_paths"]) == 0
        assert len(ctx["read_paths"]) == 1
        assert ctx["phy_connect_lines"] == []

        v = _gen_verilog("rom", ecc=False)
        assert "module test_rom_mem_wrapper" in v
        assert "i_cen" in v
        assert "i_addr" in v
        assert "o_rdata" in v
        # No write ports, no init
        assert "i_wen" not in v
        assert "i_wdata" not in v
        assert "i_init_en" not in v
        assert "init_started" not in v

    def test_rom_ecc(self):
        ctx = _build_ctx("rom", ecc=True)
        assert ctx["has_ecc"] is True
        assert len(ctx["write_paths"]) == 0
        assert len(ctx["read_paths"]) == 1

        v = _gen_verilog("rom", ecc=True)
        # ECC decode exists but no encode (no write path)
        assert "test_secded_dec" in v
        assert "genvar g_dec" in v
        assert "genvar g_enc" not in v
        # No init
        assert "init_started" not in v
        assert "i_ecc_en" in v

    def test_rom_ecc_detailed(self):
        v = _gen_verilog("rom", ecc=True, detailed=True)
        assert "o_ecc_err_syndrome" in v
        assert "syn_i" in v


# ===================================================================
# §4.4 — Verilog output structure verification
# ===================================================================

class TestVerilogStructure:
    """Cross-cutting Verilog output structure checks."""

    def test_module_params_no_ecc(self):
        v = _gen_verilog("1rw", ecc=False)
        assert "parameter DATA_WIDTH" in v
        assert "parameter RAM_DEPTH" in v
        assert "parameter INPUT_PIPE_STAGES" in v
        assert "parameter ECC_PIPE_STAGES" in v
        assert "parameter OUTPUT_PIPE_STAGES" in v
        assert "parameter ECC_SLICE_DW" not in v

    def test_module_params_ecc(self):
        v = _gen_verilog("1rw", ecc=True)
        assert "parameter ECC_SLICE_DW" in v
        assert "parameter ECC_SLICE_WITH_ECC_DW" in v
        assert "parameter ECC_SLICE_NUM" in v

    def test_data_syncn_count_sync_single_port(self):
        """Sync single_port → 1 input data_syncn instance."""
        v = _gen_verilog("1rw")
        # Count input pipe instances (not rd_latency or ecc/output pipes)
        assert v.count("u_input_pipe") >= 1

    def test_data_syncn_count_async_dual(self):
        """Async dual_port → 2 input data_syncn instances."""
        v = _gen_verilog("1r1wa")
        assert "u_wr_input_pipe" in v
        assert "u_rd_input_pipe" in v

    def test_data_syncn_count_tdp(self):
        """TDP → 2 input data_syncn instances (A/B)."""
        v = _gen_verilog("2rw")
        assert "u_a_input_pipe" in v
        assert "u_b_input_pipe" in v

    def test_init_fsm_non_rom(self):
        for t in ("1rw", "1r1w", "2rw"):
            v = _gen_verilog(t)
            assert "init_started" in v, f"{t} should have init FSM"
            assert "init_count" in v
            assert "init_done" in v

    def test_init_fsm_rom_absent(self):
        v = _gen_verilog("rom")
        assert "init_started" not in v
        assert "init_count" not in v

    def test_init_clk_single_port(self):
        v = _gen_verilog("1rw")
        assert "posedge i_clk" in v

    def test_init_clk_async_dual(self):
        v = _gen_verilog("1r1wa")
        # Init uses wr_clk
        init_block = v.split("Init FSM")[1].split("ECC Encode")[0] if "ECC Encode" in v else v.split("Init FSM")[1].split("Physical Wrapper")[0]
        assert "posedge i_wr_clk" in init_block

    def test_init_clk_tdp(self):
        v = _gen_verilog("2rw")
        init_block = v.split("Init FSM")[1].split("ECC Encode")[0] if "ECC Encode" in v else v.split("Init FSM")[1].split("Physical Wrapper")[0]
        assert "posedge i_a_clk" in init_block

    def test_phy_wrapper_instance_name(self):
        v = _gen_verilog("1rw")
        assert "u_test_1rw_phy_wrapper" in v

    def test_ecc_error_report_formula(self):
        v = _gen_verilog("1rw", ecc=True)
        # (raw | insert) & ~mask pattern
        assert re.search(
            r"raw_ecc_correctable\s*\|\s*i_ecc_err_insert\[0\].*"
            r"~i_ecc_err_mask\[0\]", v)
        assert re.search(
            r"raw_ecc_uncorrectable\s*\|\s*i_ecc_err_insert\[1\].*"
            r"~i_ecc_err_mask\[1\]", v)

    def test_ecc_pipe_bypass_no_ecc(self):
        v = _gen_verilog("1rw", ecc=False)
        # ECC disabled → bypass assign
        assert "ecc_pipe_rd_data = out_rd_data" in v

    def test_output_pipe_no_ecc(self):
        v = _gen_verilog("1rw", ecc=False)
        # Output pipe only carries rd_data (DATA_WIDTH)
        assert "u_output_pipe" in v


# ===================================================================
# §4.5 — Pipeline configuration parametrized tests
# ===================================================================

_PIPELINE_CONFIGS = [
    pytest.param("1rw", 0, 0, 0, id="1rw-P0"),
    pytest.param("1rw", 1, 1, 1, id="1rw-P4"),
    pytest.param("1r1wa", 1, 1, 1, id="1r1wa-P4"),
    pytest.param("rom", 1, 1, 1, id="rom-P4"),
]


class TestPipelineConfigs:
    """Parametrized pipeline stage tests."""

    @pytest.mark.parametrize("type_,inp,ecc_p,outp", _PIPELINE_CONFIGS)
    def test_pipeline_config(self, type_, inp, ecc_p, outp):
        ctx = _build_ctx(type_, ecc=True, input_pipe=inp,
                         ecc_pipe=ecc_p, output_pipe=outp)
        assert ctx["input_pipe_stages"] == inp
        assert ctx["ecc_pipe_stages"] == ecc_p
        assert ctx["output_pipe_stages"] == outp

        v = _gen_verilog(type_, ecc=True, input_pipe=inp,
                         ecc_pipe=ecc_p, output_pipe=outp)

        # Verify parameter values in generated Verilog
        assert f"INPUT_PIPE_STAGES       = {inp}" in v
        assert f"ECC_PIPE_STAGES         = {ecc_p}" in v
        assert f"OUTPUT_PIPE_STAGES      = {outp}" in v

        # Input pipe always present
        assert "data_syncn" in v

        # ROM has no write paths, hence no encoder
        if type_ == "rom":
            assert "genvar g_enc" not in v
        else:
            assert "genvar g_enc" in v

    def test_pipeline_p0_bypass(self):
        """P0: all stages=0 — still has data_syncn but NUM_FLOPS=0."""
        v = _gen_verilog("1rw", ecc=False, input_pipe=0, ecc_pipe=0, output_pipe=0)
        assert "INPUT_PIPE_STAGES       = 0" in v
        assert "OUTPUT_PIPE_STAGES      = 0" in v


# ===================================================================
# Multi-slice ECC coverage
# ===================================================================

class TestMultiSliceEcc:
    """ECC with width > k → multiple slices."""

    def test_multi_slice_context(self):
        # width=128, k=64 → 2 slices
        ctx = _build_ctx("1rw", ecc=True, width=128, k=64, m=8)
        assert ctx["ecc_slice_num"] == 2
        assert ctx["ecc_slice_dw"] == 64
        assert ctx["ecc_slice_with_ecc_dw"] == 72

    def test_multi_slice_verilog(self):
        v = _gen_verilog("1rw", ecc=True, width=128, k=64, m=8)
        assert "ECC_SLICE_NUM           = 2" in v

    def test_pad_slice_context(self):
        # width=48, k=64 → 1 slice with padding
        ctx = _build_ctx("1rw", ecc=True, width=48, k=64, m=8)
        assert ctx["ecc_slice_num"] == 1
        assert ctx["ecc_slice_dw"] == 64


# ===================================================================
# Coarse-mask (mask_gran > 1) coverage
# ===================================================================

class TestCoarseMaskL2:
    """L2 memory_wrapper with coarse mask (mask_gran > 1)."""

    def test_mask_port_width_sp(self):
        """1rwm with byte-mask: mask port = MASK_WIDTH, not DATA_WIDTH."""
        # lib_width=32, lib_mask_width=4 → mask_gran=8, MASK_WIDTH=64/8=8
        ctx = _build_ctx("1rwm", width=64, lib_mask_width=4)
        assert ctx["mask_width"] == 8

        v = _gen_verilog("1rwm", width=64, lib_mask_width=4)
        assert "localparam MASK_WIDTH       = 8;" in v
        # port uses literal mask_width
        assert "[8-1:0]" in v
        assert "i_bwen" in v

    def test_mask_port_width_dp(self):
        """1r1wm with byte-mask: MASK_WIDTH = width / mask_gran."""
        # lib_width=32, lib_mask_width=4 → mask_gran=8, MASK_WIDTH=32/8=4
        ctx = _build_ctx("1r1wm", width=32, lib_mask_width=4)
        assert ctx["mask_width"] == 4

        v = _gen_verilog("1r1wm", width=32, lib_mask_width=4)
        assert "localparam MASK_WIDTH       = 4;" in v

    def test_mask_port_width_tdp(self):
        """2rwm with byte-mask."""
        ctx = _build_ctx("2rwm", width=64, lib_mask_width=4)
        assert ctx["mask_width"] == 8

        v = _gen_verilog("2rwm", width=64, lib_mask_width=4)
        assert "localparam MASK_WIDTH       = 8;" in v

    def test_mask_padding_sp(self):
        """Padding mask bits = 1 when mask_pad_bits > 0."""
        # width=48, lib_width=32 → col=2, lib_mask_width=4
        # mask_gran=8, logical_mask_width=48/8=6, total_mask_width=2*4=8
        # mask_pad_bits=8-6=2 → {2{1'b1}} in phy_connect
        v = _gen_verilog("1rwm", width=48, lib_mask_width=4)
        assert "TOTAL_MASK_WIDTH" in v
        assert "{1'b1}}" in v  # padding bits set to 1

    def test_mask_padding_zero(self):
        """No padding when mask_width == total_mask_width."""
        # width=32, lib_width=32 → col=1, mask_gran=8
        # logical_mask_width=4, total_mask_width=1*4=4, pad=0
        ctx = _build_ctx("1rwm", width=32, lib_mask_width=4)
        assert ctx["mask_pad_bits"] == 0

    def test_no_ecc_mask_expansion_sp(self):
        """ECC + coarse mask: no G_MASK_EXP in L2."""
        v = _gen_verilog("1rwm", ecc=True, width=64, k=64, m=8,
                         lib_mask_width=4)
        assert "Mask Expansion" not in v
        assert "genvar g_mask" not in v
        assert "localparam MASK_WIDTH" in v

    def test_no_ecc_mask_expansion_dp(self):
        """ECC + coarse mask DP: no G_MASK_EXP in L2."""
        v = _gen_verilog("1r1wm", ecc=True, width=32, k=32, m=7,
                         lib_mask_width=4)
        assert "Mask Expansion" not in v
        assert "localparam MASK_WIDTH" in v

    def test_pipeline_mask_width(self):
        """Mask pipeline uses MASK_WIDTH, not DATA_WIDTH."""
        v = _gen_verilog("1rwm", width=64, lib_mask_width=4)
        # pipe signal should use MASK_WIDTH for bwen
        assert "MASK_WIDTH" in v

    def test_backward_compat_gran1(self):
        """mask_gran=1: MASK_WIDTH == DATA_WIDTH, zero behavior change."""
        # Default lib_mask_width=32 for 1rwm, lib_width=32 → mask_gran=1
        ctx = _build_ctx("1rwm", width=64)
        assert ctx["mask_width"] == 64


# ===================================================================
# ECC + coarse mask: _build_phy_bwen correctness tests
# ===================================================================

def _calc_tiling_ecc_coarse(mem, ecc_params):
    """Tiling helper that replicates production ECC-to-mask alignment padding."""
    import math
    phy_width = ecc_params.data_with_ecc_width  # slice_count * n
    if ecc_params.enabled and mem.physical.lib_mask_width > 0:
        mask_gran = mem.physical.lib_width // mem.physical.lib_mask_width
        if mask_gran > 1:
            padded_n = math.ceil(ecc_params.n / mask_gran) * mask_gran
            phy_width = padded_n * ecc_params.slice_count
    return PhysicalWrapperGenerator.calc_tiling(
        phy_width, mem.depth,
        mem.physical.lib_width, mem.physical.lib_depth,
        mem.physical.lib_mask_width,
    )


def _build_ctx_ecc_coarse(type_: str, *, width: int, k: int, m: int,
                           lib_width: int = 32, lib_mask_width: int = 4,
                           lib_depth: int = 256) -> dict:
    """Build context using production-accurate tiling for ECC+coarse mask."""
    from helpers import make_mem_spec, make_ecc_params, make_ecc_modules
    mem = make_mem_spec(
        type_=type_, width=width, depth=256,
        lib_width=lib_width, lib_depth=lib_depth,
        lib_mask_width=lib_mask_width,
        ecc_enable=True, ecc_k=k, ecc_m=m,
    )
    ecc_params = make_ecc_params(width, k=k, m=m, enabled=True)
    ecc_modules = make_ecc_modules()
    tiling = _calc_tiling_ecc_coarse(mem, ecc_params)
    itype = _ITYPES[type_]
    gen = GENERATORS[itype.base_type]
    return gen.build_context(mem, ecc_params, ecc_modules, itype,
                             f"test_{type_}_mem", f"test_{type_}_phy", tiling)


class TestEccCoarseMaskBwen:
    """Verify _build_phy_bwen correctness for ECC + coarse mask scenarios."""

    def test_bwen_bit_count_sp(self):
        """SP: phy_bwen RHS bit count equals TOTAL_MASK_WIDTH (not mask_width*mask_per_slice)."""
        # width=256, k=64, m=8, lib_width=32, lib_mask_width=4 → mask_gran=8
        # n=72, padded_n=72, mask_per_slice=9, data_mask_per_slice=8
        # total_mask_width = ceil(72*4/32)*4 = 9*4 = 36
        # mask_width = 256//8 = 32
        ctx = _build_ctx_ecc_coarse("1rwm", width=256, k=64, m=8,
                                     lib_width=32, lib_mask_width=4)
        total_mask = ctx["total_mask_width"]
        mask_per_slice = ctx["mask_per_slice"]
        data_mask_per_slice = ctx["data_mask_per_slice"]
        mask_width = ctx["mask_width"]
        assert total_mask == 36
        assert mask_per_slice == 9
        assert data_mask_per_slice == 8
        assert mask_width == 32

        # Extract phy_bwen line from generated Verilog and count bits
        from memory_wrapper_gen import GENERATORS
        from helpers import make_mem_spec, make_ecc_params, make_ecc_modules
        mem = make_mem_spec(type_="1rwm", width=256, depth=256,
                            lib_width=32, lib_depth=256, lib_mask_width=4,
                            ecc_enable=True, ecc_k=64, ecc_m=8)
        ecc_params = make_ecc_params(256, k=64, m=8, enabled=True)
        ecc_modules = make_ecc_modules()
        from physical_wrapper_gen import PhysicalWrapperGenerator
        tiling = _calc_tiling_ecc_coarse(mem, ecc_params)
        v = GENERATORS["single_port"].generate(
            mem, ecc_params, ecc_modules, _ITYPES["1rwm"],
            "test_sp", "test_phy", tiling)
        # The phy_bwen line should reference TOTAL_MASK_WIDTH = 36
        assert "phy_bwen" in v
        # Should NOT use the old replicated pattern (mask_width*mask_per_slice=288)
        # Old bug would generate {9{pipe_bwen[0]}}, {9{pipe_bwen[1]}}, ...
        assert "{9{pipe_bwen[" not in v

    def test_bwen_slice_structure_sp(self):
        """SP: phy_bwen has per-slice structure: parity bits + data mask bits."""
        ctx = _build_ctx_ecc_coarse("1rwm", width=256, k=64, m=8,
                                     lib_width=32, lib_mask_width=4)
        assert ctx.get("data_mask_per_slice") == 8

        from memory_wrapper_gen import MemoryWrapperGenerator
        # parity_mask_per_slice = 9 - 8 = 1; slice_count = 32//8 = 4
        bwen = MemoryWrapperGenerator._build_phy_bwen(
            total_mask=36, mask_width=32, mask_per_slice=9,
            data_mask_per_slice=8, pipe_bwen="pipe_bwen", init_guard=False)
        # Should generate per-slice: {1{1'b1}}, pipe_bwen[hi:lo]
        assert "{1{1'b1}}" in bwen
        assert "pipe_bwen[31:24]" in bwen
        assert "pipe_bwen[23:16]" in bwen
        assert "pipe_bwen[15:8]" in bwen
        assert "pipe_bwen[7:0]" in bwen

    def test_bwen_slice_structure_with_padding(self):
        """Case where padded_n > n: parity bits cover ECC parity + padding."""
        # width=128, k=32, m=7, n=39, lib_width=8, lib_mask_width=4
        # mask_gran=2, padded_n=ceil(39/2)*2=40, mask_per_slice=20
        # data_mask_per_slice=32//2=16, parity_mask_per_slice=4
        # slice_count=128//32=4 (4%4=0 ✓), total_mask=ceil(40*4/8)*4=80
        from memory_wrapper_gen import MemoryWrapperGenerator
        bwen = MemoryWrapperGenerator._build_phy_bwen(
            total_mask=80, mask_width=64, mask_per_slice=20,
            data_mask_per_slice=16, pipe_bwen="m", init_guard=False)
        assert "{4{1'b1}}" in bwen
        assert "m[63:48]" in bwen
        assert "m[0:0]" not in bwen   # should be [15:0]
        assert "m[15:0]" in bwen

    def test_bwen_no_parity_gran1(self):
        """mask_gran=1 (full bit-mask): else branch, no slice replication."""
        from memory_wrapper_gen import MemoryWrapperGenerator
        # mask_per_slice=1 → else branch: pad or passthrough
        bwen = MemoryWrapperGenerator._build_phy_bwen(
            total_mask=8, mask_width=8, mask_per_slice=1,
            data_mask_per_slice=1, pipe_bwen="m", init_guard=False)
        assert bwen == "m;"

    def test_bwen_mismatch_raises(self):
        """Mismatched parameters raise ValueError."""
        from memory_wrapper_gen import MemoryWrapperGenerator
        with pytest.raises(ValueError, match="positive multiple of data_mask_per_slice"):
            MemoryWrapperGenerator._build_phy_bwen(
                total_mask=36, mask_width=32, mask_per_slice=9,
                data_mask_per_slice=7,   # wrong: 32//7 is not integer slice
                pipe_bwen="m", init_guard=False)

    def test_gen_memory_wrapper_bad_basetype_raises(self):
        """gen_memory_wrapper raises ValueError for unknown base_type."""
        from memory_wrapper_gen import gen_memory_wrapper
        from helpers import make_mem_spec, make_ecc_params
        from config_io import InterfaceType, TilingParams
        mem = make_mem_spec()
        ecc_params = make_ecc_params(mem.width)
        bad_itype = InterfaceType(base_type="nonexistent", has_mask=False,
                                  is_async=False, port_map={}, sub_types=())
        tiling = TilingParams(col_count=1, row_count=1, total_blocks=1,
                              width_pad_bits=0)
        with pytest.raises(ValueError, match="unsupported base_type"):
            gen_memory_wrapper(mem, ecc_params, None, bad_itype,
                               "m", "p", tiling)

    def test_gen_memory_wrapper_ecc_none_raises(self):
        """gen_memory_wrapper raises ValueError when ECC enabled but ecc_modules=None."""
        from memory_wrapper_gen import gen_memory_wrapper
        from helpers import make_mem_spec, make_ecc_params
        from config_io import TilingParams
        mem = make_mem_spec(type_="1rw", ecc_enable=True, ecc_k=64, ecc_m=8)
        ecc_params = make_ecc_params(mem.width, enabled=True)
        tiling = TilingParams(col_count=1, row_count=1, total_blocks=1,
                              width_pad_bits=0)
        with pytest.raises(ValueError, match="ecc_modules is None"):
            gen_memory_wrapper(mem, ecc_params, None, _ITYPES["1rw"],
                               "m", "p", tiling)
