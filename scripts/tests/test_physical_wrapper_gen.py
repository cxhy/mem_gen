"""Unit tests for physical_wrapper_gen.py — helpers + 4 generator classes."""

from __future__ import annotations

import re

import pytest

from config_io import SubTypeInfo
from physical_wrapper_gen import (
    PhysicalWrapperGenerator,
    gen_physical_wrapper,
)

from helpers import make_ecc_params, make_mem_spec


# ===================================================================
# Helper: generate Verilog for a given type + tiling scenario
# ===================================================================

def _generate(type_: str, width: int, depth: int,
              interface_types: dict, lib_width: int = 32,
              lib_depth: int = 256) -> str:
    mem = make_mem_spec(type_=type_, width=width, depth=depth,
                        lib_width=lib_width, lib_depth=lib_depth)
    itype = interface_types[type_]
    ecc = make_ecc_params(width)
    tiling = PhysicalWrapperGenerator.calc_tiling(
        width, depth, lib_width, lib_depth,
        mem.physical.lib_mask_width,
    )
    module_name = f"{mem.name}_physical_wrapper"
    return gen_physical_wrapper(mem, ecc, tiling, itype, module_name)


# ===================================================================
# TestHelpers — calc_tiling, _format_const_value, _data_slice_expr,
#               _mask_slice_expr, _build_cell_ports
# ===================================================================

class TestCalcTiling:
    calc = staticmethod(PhysicalWrapperGenerator.calc_tiling)

    def test_1x1(self):
        t = self.calc(32, 256, 32, 256)
        assert (t.col_count, t.row_count, t.width_pad_bits) == (1, 1, 0)

    def test_col_exact(self):
        t = self.calc(64, 256, 32, 256)
        assert (t.col_count, t.row_count, t.width_pad_bits) == (2, 1, 0)

    def test_col_pad(self):
        t = self.calc(48, 256, 32, 256)
        assert (t.col_count, t.row_count, t.width_pad_bits) == (2, 1, 16)

    def test_row_exact(self):
        t = self.calc(32, 512, 32, 256)
        assert (t.col_count, t.row_count, t.width_pad_bits) == (1, 2, 0)

    def test_row_pad(self):
        t = self.calc(32, 384, 32, 256)
        assert (t.col_count, t.row_count) == (1, 2)

    def test_both_exact(self):
        t = self.calc(64, 512, 32, 256)
        assert (t.col_count, t.row_count, t.width_pad_bits) == (2, 2, 0)
        assert t.total_blocks == 4

    def test_both_pad(self):
        t = self.calc(48, 384, 32, 256)
        assert (t.col_count, t.row_count, t.width_pad_bits) == (2, 2, 16)

    def test_mask(self):
        t = self.calc(48, 256, 32, 256, lib_mask_width=32)
        assert t.total_mask_width == 64
        assert t.mask_pad_bits == 16

    def test_mask_exact(self):
        t = self.calc(64, 256, 32, 256, lib_mask_width=32)
        assert t.total_mask_width == 64
        assert t.mask_pad_bits == 0


class TestFormatConstValue:
    fmt = staticmethod(PhysicalWrapperGenerator._format_const_value)

    def test_zeros(self):
        assert self.fmt("zeros", 32) == "{32{1'b0}}"

    def test_ones(self):
        assert self.fmt("ones", 32) == "{32{1'b1}}"

    def test_int(self):
        assert self.fmt(0, 32) == "0"

    def test_str(self):
        assert self.fmt("{2'b10}", 32) == "{2'b10}"


class TestDataSliceExpr:
    fn = staticmethod(PhysicalWrapperGenerator._data_slice_expr)

    def test_normal(self):
        result = self.fn(0, 2, 32, 64, 0, "wdata")
        assert result == "wdata[31:0]"

    def test_last_col_no_pad(self):
        result = self.fn(1, 2, 32, 64, 0, "wdata")
        assert result == "wdata[63:32]"

    def test_last_col_with_pad(self):
        result = self.fn(1, 2, 32, 48, 16, "wdata")
        assert result == "{16'b0, wdata[47:32]}"


class TestMaskSliceExpr:
    fn = staticmethod(PhysicalWrapperGenerator._mask_slice_expr)

    def test_first_col(self):
        result = self.fn(0, 32, "bwen")
        assert result == "bwen[31:0]"

    def test_second_col(self):
        result = self.fn(1, 32, "bwen")
        assert result == "bwen[63:32]"


class TestBuildCellPorts:
    fn = staticmethod(PhysicalWrapperGenerator._build_cell_ports)

    def test_basic(self):
        func_ports = [".CLK (clk)", ".CEB (~cen)"]
        sub = SubTypeInfo(
            names=("test",),
            const_ports={"BWEB": "zeros", "SCAN_EN": 0},
            output_ports=("SCAN_OUT_C",),
        )
        result = self.fn(func_ports, sub, 32)
        assert ".CLK (clk)" in result
        assert ".CEB (~cen)" in result
        assert ".BWEB ({32{1'b0}})" in result
        assert ".SCAN_EN (0)" in result
        assert ".SCAN_OUT_C ()" in result


# ===================================================================
# Common assertion helpers for generator integration tests
# ===================================================================

def _assert_structure(v: str, module_name: str, col: int, row: int):
    """Check module name, endmodule, cell count and naming."""
    assert f"module {module_name}" in v
    assert "endmodule" in v
    for r in range(row):
        for c in range(col):
            assert f"u_mem_r{r}_c{c}" in v
    total = len(re.findall(r"u_mem_r\d+_c\d+", v))
    # Each cell appears twice: instance name + port block
    assert total >= col * row


def _assert_row_sel(v: str, row_count: int, prefix: str = ""):
    """Check row_sel register and case mux presence."""
    if row_count > 1:
        assert f"{prefix}rd_row_sel_d" in v
        assert "case" in v
    else:
        assert "case" not in v


def _assert_pad(v: str, width_pad_bits: int):
    """Check pad bits in data slice."""
    if width_pad_bits > 0:
        assert f"{width_pad_bits}'b0" in v


def _assert_const_ports(v: str, interface_types: dict, type_: str):
    """Check all const_ports appear in cell instantiation."""
    itype = interface_types[type_]
    sub = itype.sub_types[0]
    for pin in sub.const_ports:
        assert f".{pin}" in v


def _assert_output_ports(v: str, interface_types: dict, type_: str):
    """Check all output_ports appear as unconnected."""
    itype = interface_types[type_]
    sub = itype.sub_types[0]
    for pin in sub.output_ports:
        assert f".{pin} ()" in v


# ===================================================================
# TestSinglePort — 1rw, 1rwm
# ===================================================================

class TestSinglePort:

    def test_1rw_T1(self, interface_types):
        v = _generate("1rw", 32, 256, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 1, 1)
        assert "bwen" not in v.split("endmodule")[0].split(")(")[0]
        assert ".BWEB ({32{1'b0}})" in v
        _assert_const_ports(v, interface_types, "1rw")
        _assert_output_ports(v, interface_types, "1rw")
        _assert_row_sel(v, 1)

    def test_1rw_T3(self, interface_types):
        v = _generate("1rw", 48, 256, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 1)
        _assert_pad(v, 16)

    def test_1rw_T4(self, interface_types):
        v = _generate("1rw", 32, 512, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 1, 2)
        _assert_row_sel(v, 2)

    def test_1rw_T9(self, interface_types):
        v = _generate("1rw", 48, 384, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 2)
        _assert_pad(v, 16)
        _assert_row_sel(v, 2)

    def test_1rwm_T1(self, interface_types):
        v = _generate("1rwm", 32, 256, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 1, 1)
        # mask type: module ports should have bwen
        port_section = v.split(");")[0]
        assert "bwen" in port_section
        # BWEB should be connected to mask, not tied off
        assert ".BWEB (~(bwen[31:0]))" in v

    def test_1rwm_T6(self, interface_types):
        v = _generate("1rwm", 64, 512, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 2)
        assert "bwen[31:0]" in v
        assert "bwen[63:32]" in v

    def test_1rwm_T9(self, interface_types):
        v = _generate("1rwm", 48, 384, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 2)
        _assert_pad(v, 16)
        _assert_row_sel(v, 2)
        assert "bwen" in v.split(");")[0]


# ===================================================================
# TestDualPort — 1r1w, 1r1wm, 1r1wa, 1r1wma
# ===================================================================

class TestDualPort:

    def test_1r1w_T1(self, interface_types):
        v = _generate("1r1w", 32, 256, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 1, 1)
        port_section = v.split(");")[0]
        assert "wr_en" in port_section
        assert "rd_en" in port_section
        assert "wr_addr" in port_section
        assert "rd_addr" in port_section
        assert "input                          clk" in v
        _assert_row_sel(v, 1)

    def test_1r1w_T5(self, interface_types):
        v = _generate("1r1w", 32, 384, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 1, 2)
        _assert_row_sel(v, 2)
        assert "posedge clk" in v

    def test_1r1w_T9(self, interface_types):
        v = _generate("1r1w", 48, 384, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 2)
        _assert_pad(v, 16)
        _assert_row_sel(v, 2)

    def test_1r1wm_T6(self, interface_types):
        v = _generate("1r1wm", 64, 512, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 2)
        port_section = v.split(");")[0]
        assert "wr_mask" in port_section
        assert "wr_mask[31:0]" in v
        assert "wr_mask[63:32]" in v

    def test_1r1wm_T9(self, interface_types):
        v = _generate("1r1wm", 48, 384, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 2)
        _assert_pad(v, 16)
        assert "wr_mask" in v.split(");")[0]

    def test_1r1wa_T1(self, interface_types):
        v = _generate("1r1wa", 32, 256, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 1, 1)
        port_section = v.split(");")[0]
        assert "wr_clk" in port_section
        assert "rd_clk" in port_section
        assert "input                          clk," not in v

    def test_1r1wa_T4(self, interface_types):
        v = _generate("1r1wa", 32, 512, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 1, 2)
        _assert_row_sel(v, 2)
        assert "posedge rd_clk" in v

    def test_1r1wma_T9(self, interface_types):
        v = _generate("1r1wma", 48, 384, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 2)
        _assert_pad(v, 16)
        port_section = v.split(");")[0]
        assert "wr_clk" in port_section
        assert "rd_clk" in port_section
        assert "wr_mask" in port_section
        assert "posedge rd_clk" in v


# ===================================================================
# TestTrueDualPort — 2rw, 2rwm
# ===================================================================

class TestTrueDualPort:

    def test_2rw_T1(self, interface_types):
        v = _generate("2rw", 32, 256, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 1, 1)
        port_section = v.split(");")[0]
        assert "a_clk" in port_section
        assert "b_clk" in port_section
        assert "a_wen" in port_section
        assert "b_wen" in port_section
        _assert_const_ports(v, interface_types, "2rw")
        _assert_output_ports(v, interface_types, "2rw")

    def test_2rw_T3(self, interface_types):
        v = _generate("2rw", 48, 256, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 1)
        _assert_pad(v, 16)
        assert "a_wdata[47:32]" in v
        assert "b_wdata[47:32]" in v

    def test_2rw_T5(self, interface_types):
        v = _generate("2rw", 32, 384, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 1, 2)
        assert "a_rd_row_sel_d" in v
        assert "b_rd_row_sel_d" in v
        assert "posedge a_clk" in v
        assert "posedge b_clk" in v

    def test_2rw_T9(self, interface_types):
        v = _generate("2rw", 48, 384, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 2)
        _assert_pad(v, 16)
        assert "a_rd_row_sel_d" in v
        assert "b_rd_row_sel_d" in v

    def test_2rwm_T1(self, interface_types):
        v = _generate("2rwm", 32, 256, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 1, 1)
        port_section = v.split(");")[0]
        assert "a_bwen" in port_section
        assert "b_bwen" in port_section

    def test_2rwm_T6(self, interface_types):
        v = _generate("2rwm", 64, 512, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 2)
        assert "a_bwen[31:0]" in v
        assert "b_bwen[63:32]" in v

    def test_2rwm_T9(self, interface_types):
        v = _generate("2rwm", 48, 384, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 2)
        _assert_pad(v, 16)
        port_section = v.split(");")[0]
        assert "a_bwen" in port_section
        assert "b_bwen" in port_section


# ===================================================================
# TestRom — rom
# ===================================================================

class TestRom:

    def test_rom_T1(self, interface_types):
        v = _generate("rom", 32, 256, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 1, 1)
        port_section = v.split(");")[0]
        assert "cen" in port_section
        assert "addr" in port_section
        assert "rdata" in port_section
        assert "wen" not in port_section
        assert "wdata" not in port_section
        _assert_const_ports(v, interface_types, "rom")
        _assert_output_ports(v, interface_types, "rom")

    def test_rom_T3(self, interface_types):
        v = _generate("rom", 48, 256, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 1)
        # ROM has no wdata, pad is implicit via read mux truncation
        assert "rdata = row_0_rd_data[47:0]" in v

    def test_rom_T4(self, interface_types):
        v = _generate("rom", 32, 512, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 1, 2)
        _assert_row_sel(v, 2)

    def test_rom_T9(self, interface_types):
        v = _generate("rom", 48, 384, interface_types)
        _assert_structure(v, "test_mem_physical_wrapper", 2, 2)
        # ROM pad via read mux truncation
        assert "rd_data_mux[47:0]" in v
        _assert_row_sel(v, 2)


# ===================================================================
# TestParameterValues — DATA_WIDTH / ADDR_WIDTH correctness
# ===================================================================

class TestParameterValues:

    def test_data_width(self, interface_types):
        v = _generate("1rw", 48, 256, interface_types)
        assert "parameter DATA_WIDTH  = 48" in v

    def test_addr_width_256(self, interface_types):
        v = _generate("1rw", 32, 256, interface_types)
        assert "parameter ADDR_WIDTH  = 8" in v

    def test_addr_width_512(self, interface_types):
        v = _generate("1rw", 32, 512, interface_types)
        assert "parameter ADDR_WIDTH  = 9" in v


# ===================================================================
# TestPolarity — ~ prefix inversion
# ===================================================================

class TestPolarity:

    def test_ceb_inverted(self, interface_types):
        v = _generate("1rw", 32, 256, interface_types)
        assert ".CEB (~(cen))" in v

    def test_web_inverted(self, interface_types):
        v = _generate("1rw", 32, 256, interface_types)
        assert ".WEB (~(wen))" in v

    def test_clk_not_inverted(self, interface_types):
        v = _generate("1rw", 32, 256, interface_types)
        assert ".CLK (clk)" in v


# ===================================================================
# Helpers for coarse-mask tests
# ===================================================================

def _generate_coarse_mask(type_: str, width: int, depth: int,
                           interface_types: dict, lib_width: int,
                           lib_mask_width: int) -> str:
    """Generate wrapper with an explicit lib_mask_width (coarse mask)."""
    mem = make_mem_spec(type_=type_, width=width, depth=depth,
                        lib_width=lib_width, lib_depth=256,
                        lib_mask_width=lib_mask_width)
    itype = interface_types[type_]
    ecc = make_ecc_params(width)
    tiling = PhysicalWrapperGenerator.calc_tiling(
        width, depth, lib_width, 256, lib_mask_width,
    )
    module_name = f"{mem.name}_physical_wrapper"
    return gen_physical_wrapper(mem, ecc, tiling, itype, module_name)


# ===================================================================
# TestMaskExpansion — coarse mask → bit-level generate block
# ===================================================================

class TestMaskExpansion:
    """Verify that mask_gran > 1 triggers RTL generate expansion blocks."""

    def test_bit_level_no_expand_sp(self, interface_types):
        """mask_gran=1 (lib_mask_width==lib_width): no G_MASK_EXP block."""
        v = _generate_coarse_mask("1rwm", 32, 256, interface_types, 32, 32)
        assert "G_MASK_EXP" not in v
        assert "genvar" not in v
        assert ".BWEB (~(bwen[31:0]))" in v

    def test_byte_mask_single_port(self, interface_types):
        """1rwm byte-mask (mask_gran=8): wire, genvar, generate block, cell connect."""
        v = _generate_coarse_mask("1rwm", 32, 256, interface_types, 32, 4)
        # Expansion wire declared
        assert "wire [31:0] expanded_bwen_r0_c0" in v
        # Generate block label and genvar
        assert "G_MASK_EXP_R0_C0" in v
        assert "genvar g_bwen_r0_c0" in v
        # Assignment: shift × gran and replication
        assert "g_bwen_r0_c0 * 8 +: 8" in v
        assert "{8{bwen[0 + g_bwen_r0_c0]}}" in v
        # Cell port connected to expanded wire (inverted, per fixture port_map ~BWEB)
        assert ".BWEB (~(expanded_bwen_r0_c0))" in v
        # Module-level mask port uses total_mask_width=4
        assert "input  [4-1:0]" in v

    def test_byte_mask_dual_port(self, interface_types):
        """1r1wm byte-mask: expansion uses wr_mask signal."""
        v = _generate_coarse_mask("1r1wm", 32, 256, interface_types, 32, 4)
        assert "wire [31:0] expanded_bwen_r0_c0" in v
        assert "{8{wr_mask[0 + g_bwen_r0_c0]}}" in v
        assert ".BWEB (~(expanded_bwen_r0_c0))" in v

    def test_byte_mask_true_dual_port(self, interface_types):
        """2rwm byte-mask: separate a_ / b_ expansion wires with _A / _B labels."""
        v = _generate_coarse_mask("2rwm", 32, 256, interface_types, 32, 4)
        # Port A expansion
        assert "wire [31:0] a_expanded_bwen_r0_c0" in v
        assert "G_MASK_EXP_R0_C0_A" in v
        assert "{8{a_bwen[0 + g_bwen_r0_c0_a]}}" in v
        # Port B expansion
        assert "wire [31:0] b_expanded_bwen_r0_c0" in v
        assert "G_MASK_EXP_R0_C0_B" in v
        assert "{8{b_bwen[0 + g_bwen_r0_c0_b]}}" in v

    def test_multi_col_unique_labels(self, interface_types):
        """1rwm width=64, byte-mask: two expansion blocks with distinct names/offsets."""
        v = _generate_coarse_mask("1rwm", 64, 256, interface_types, 32, 4)
        # Both columns present
        assert "G_MASK_EXP_R0_C0" in v
        assert "G_MASK_EXP_R0_C1" in v
        assert "wire [31:0] expanded_bwen_r0_c0" in v
        assert "wire [31:0] expanded_bwen_r0_c1" in v
        # Col 0 offset=0, col 1 offset=4 (lib_mask_width=4)
        assert "bwen[0 + g_bwen_r0_c0]" in v
        assert "bwen[4 + g_bwen_r0_c1]" in v

    def test_multi_row_multi_col_labels(self, interface_types):
        """1rwm 2×2 tiling, byte-mask: 4 unique expansion blocks."""
        v = _generate_coarse_mask("1rwm", 64, 512, interface_types, 32, 4)
        for r in range(2):
            for c in range(2):
                assert f"G_MASK_EXP_R{r}_C{c}" in v
                assert f"wire [31:0] expanded_bwen_r{r}_c{c}" in v


class TestCalcTilingDefense:
    """calc_tiling input validation."""
    calc = staticmethod(PhysicalWrapperGenerator.calc_tiling)

    def test_zero_logical_width_raises(self):
        with pytest.raises(ValueError, match="logical_width must be positive"):
            self.calc(0, 256, 32, 256)

    def test_negative_depth_raises(self):
        with pytest.raises(ValueError, match="depth must be positive"):
            self.calc(32, -1, 32, 256)

    def test_zero_lib_width_raises(self):
        with pytest.raises(ValueError, match="lib_width must be positive"):
            self.calc(32, 256, 0, 256)

    def test_zero_lib_depth_raises(self):
        with pytest.raises(ValueError, match="lib_depth must be positive"):
            self.calc(32, 256, 32, 0)


class TestGenPhysicalDefense:
    """gen_physical_wrapper entry point defense."""

    def test_bad_basetype_raises(self, interface_types):
        from physical_wrapper_gen import gen_physical_wrapper
        from config_io import InterfaceType, TilingParams, EccParams
        from helpers import make_mem_spec
        mem = make_mem_spec()
        ecc = EccParams(enabled=False, logical_total_width=mem.width)
        tiling = TilingParams(col_count=1, row_count=1, total_blocks=1,
                              width_pad_bits=0)
        bad_itype = InterfaceType(base_type="unknown", has_mask=False,
                                  is_async=False, port_map={}, sub_types=())
        with pytest.raises(ValueError, match="unsupported base_type"):
            gen_physical_wrapper(mem, ecc, tiling, bad_itype, "m")
