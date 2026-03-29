"""Tests for Layer 3 bypass_wrapper_gen."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config_io import EccConfig, EccParams, InterfaceType, MemorySpec, PhysicalConfig, SubTypeInfo
from bypass_wrapper_gen import DualPortBypassGen, gen_bypass_wrapper
from physical_wrapper_gen import PhysicalWrapperGenerator


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_interface_type(has_mask: bool = False) -> InterfaceType:
    port_map = {
        "wr_clk": "CLK", "wr_en": "~WEB", "wr_addr": "AA",
        "wr_data": "D", "rd_clk": "CLK", "rd_en": "~REB",
        "rd_addr": "AB", "rd_data": "Q",
    }
    if has_mask:
        port_map["wr_mask"] = "~BWEB"
    return InterfaceType(
        base_type="dual_port",
        has_mask=has_mask,
        is_async=False,
        port_map=port_map,
        sub_types=(
            SubTypeInfo(names=("uhd2prf",), const_ports={}, output_ports=()),
        ),
    )


def _make_mem_spec(
    type_: str = "1r1w",
    width: int = 32,
    depth: int = 256,
    input_pipe: int = 0,
    ram_rd_latency: int = 1,
    ecc_pipe: int = 0,
    output_pipe: int = 0,
    ecc_enable: bool = False,
    ecc_data_bits: int = 32,
    ecc_ecc_bits: int = 7,
    detailed_report: bool = False,
    lib_width: int = 32,
    lib_mask_width: int | None = None,
) -> MemorySpec:
    if lib_mask_width is None:
        lib_mask_width = lib_width if "m" in type_ else 0
    return MemorySpec(
        name="test_bypass",
        type=type_,
        width=width,
        depth=depth,
        ecc=EccConfig(
            enable=ecc_enable,
            code_type="hsiao" if ecc_enable else "",
            data_bits_per_slice=ecc_data_bits if ecc_enable else 0,
            ecc_bits_per_slice=ecc_ecc_bits if ecc_enable else 0,
            detailed_report=detailed_report,
        ),
        physical=PhysicalConfig(
            sub_type="uhd2prf",
            lib_name="TESTLIB",
            lib_width=lib_width,
            lib_depth=256,
            lib_mask_width=lib_mask_width,
        ),
        ram_rd_latency=ram_rd_latency,
        input_pipe_stages=input_pipe,
        ecc_pipe_stages=ecc_pipe,
        output_pipe_stages=output_pipe,
        enable_l2=True,
        enable_l3=True,
    )


def _make_ecc_params(enabled: bool = False, width: int = 32) -> EccParams:
    if not enabled:
        return EccParams(enabled=False, logical_total_width=width)
    return EccParams(
        enabled=True,
        logical_total_width=39,
        slice_count=1,
        data_pad_width=32,
        ecc_total_bits=7,
        data_with_ecc_width=39,
        pad_bits=0,
        k=32,
        m=7,
        n=39,
    )


def _calc_tiling(mem: MemorySpec, ecc_params: EccParams):
    if ecc_params.enabled:
        phy_width = ecc_params.data_with_ecc_width
    else:
        phy_width = mem.width
    return PhysicalWrapperGenerator.calc_tiling(
        phy_width, mem.depth,
        mem.physical.lib_width, mem.physical.lib_depth,
        mem.physical.lib_mask_width,
    )


# ---------------------------------------------------------------------------
# Context building tests
# ---------------------------------------------------------------------------

class TestDualPortBypassGenContext:
    """Tests for DualPortBypassGen.build_context()."""

    def test_basic_1r1w_context(self):
        gen = DualPortBypassGen()
        spec = _make_mem_spec()
        ecc = _make_ecc_params()
        itype = _make_interface_type()
        tiling = _calc_tiling(spec, ecc)

        ctx = gen.build_context(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert ctx["module_name"] == "test_top"
        assert ctx["l2_wrapper_name"] == "test_mem"
        assert ctx["data_width"] == 32
        assert ctx["ram_depth"] == 256
        assert ctx["has_ecc"] is False
        assert ctx["has_mask"] is False
        assert ctx["bypass_depth"] == 1  # 0+1+0+0

    def test_1r1wm_context_has_mask(self):
        gen = DualPortBypassGen()
        spec = _make_mem_spec(type_="1r1wm")
        ecc = _make_ecc_params()
        itype = _make_interface_type(has_mask=True)
        tiling = _calc_tiling(spec, ecc)

        ctx = gen.build_context(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert ctx["has_mask"] is True
        # bypass_data_width = 1 + 32 (data) + 32 (mask, gran=1)
        assert ctx["bypass_data_width"] == 1 + 32 + 32

    def test_ecc_context(self):
        gen = DualPortBypassGen()
        spec = _make_mem_spec(ecc_enable=True)
        ecc = _make_ecc_params(enabled=True)
        itype = _make_interface_type()
        tiling = _calc_tiling(spec, ecc)

        ctx = gen.build_context(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert ctx["has_ecc"] is True
        assert ctx["ecc_m"] == 7
        # L2 inst ports should include ECC signals
        l2_ports_str = " ".join(ctx["l2_inst_ports"])
        assert "i_ecc_en" in l2_ports_str
        assert "l2_ecc_correctable_valid" in l2_ports_str

    def test_ecc_detailed_report(self):
        gen = DualPortBypassGen()
        spec = _make_mem_spec(ecc_enable=True, detailed_report=True)
        ecc = _make_ecc_params(enabled=True)
        itype = _make_interface_type()
        tiling = _calc_tiling(spec, ecc)

        ctx = gen.build_context(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert ctx["detailed_report"] is True
        l2_ports_str = " ".join(ctx["l2_inst_ports"])
        assert "l2_ecc_err_syndrome" in l2_ports_str

    def test_bypass_depth_calculation(self):
        gen = DualPortBypassGen()
        spec = _make_mem_spec(
            input_pipe=2, ram_rd_latency=1, ecc_pipe=1, output_pipe=1,
        )
        ecc = _make_ecc_params()
        itype = _make_interface_type()
        tiling = _calc_tiling(spec, ecc)

        ctx = gen.build_context(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert ctx["bypass_depth"] == 5  # 2+1+1+1

    def test_module_ports_no_mask_no_ecc(self):
        gen = DualPortBypassGen()
        spec = _make_mem_spec()
        ecc = _make_ecc_params()
        itype = _make_interface_type()
        tiling = _calc_tiling(spec, ecc)

        ctx = gen.build_context(spec, ecc, itype, "test_top", "test_mem", tiling)

        ports_str = " ".join(ctx["module_ports"])
        assert "i_wr_bwen" not in ports_str
        assert "o_ecc_correctable_valid" not in ports_str

    def test_module_ports_mask(self):
        gen = DualPortBypassGen()
        spec = _make_mem_spec(type_="1r1wm")
        ecc = _make_ecc_params()
        itype = _make_interface_type(has_mask=True)
        tiling = _calc_tiling(spec, ecc)

        ctx = gen.build_context(spec, ecc, itype, "test_top", "test_mem", tiling)

        ports_str = " ".join(ctx["module_ports"])
        assert "i_wr_bwen" in ports_str

    def test_l2_inst_ports_mask(self):
        gen = DualPortBypassGen()
        spec = _make_mem_spec(type_="1r1wm")
        ecc = _make_ecc_params()
        itype = _make_interface_type(has_mask=True)
        tiling = _calc_tiling(spec, ecc)

        ctx = gen.build_context(spec, ecc, itype, "test_top", "test_mem", tiling)

        l2_ports_str = " ".join(ctx["l2_inst_ports"])
        assert "i_wr_bwen" in l2_ports_str


# ---------------------------------------------------------------------------
# Verilog generation tests
# ---------------------------------------------------------------------------

class TestBypassWrapperVerilog:
    """Tests for generated Verilog output."""

    def test_1r1w_generates_valid_verilog(self):
        spec = _make_mem_spec()
        ecc = _make_ecc_params()
        itype = _make_interface_type()
        tiling = _calc_tiling(spec, ecc)

        result = gen_bypass_wrapper(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert "module test_top" in result
        assert "test_mem u_test_mem" in result
        assert "bypass_hit" in result
        assert "delayed_hit" in result
        assert "data_syncn" in result
        assert "o_rd_data = delayed_hit ? delayed_wdata : l2_rd_data" in result
        assert "endmodule" in result

    def test_1r1wm_generates_mask_bypass(self):
        spec = _make_mem_spec(type_="1r1wm")
        ecc = _make_ecc_params()
        itype = _make_interface_type(has_mask=True)
        tiling = _calc_tiling(spec, ecc)

        result = gen_bypass_wrapper(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert "i_wr_bwen" in result
        assert "delayed_bwen" in result
        # mask_gran==1 → direct AND/OR (no expansion)
        assert "(delayed_wdata & delayed_bwen) | (l2_rd_data & ~delayed_bwen)" in result

    def test_ecc_gating(self):
        spec = _make_mem_spec(ecc_enable=True)
        ecc = _make_ecc_params(enabled=True)
        itype = _make_interface_type()
        tiling = _calc_tiling(spec, ecc)

        result = gen_bypass_wrapper(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert "l2_ecc_correctable_valid   & ~delayed_hit" in result
        assert "l2_ecc_uncorrectable_valid & ~delayed_hit" in result

    def test_ecc_detailed_report_syndrome(self):
        spec = _make_mem_spec(ecc_enable=True, detailed_report=True)
        ecc = _make_ecc_params(enabled=True)
        itype = _make_interface_type()
        tiling = _calc_tiling(spec, ecc)

        result = gen_bypass_wrapper(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert "l2_ecc_err_syndrome" in result
        assert "o_ecc_err_syndrome" in result

    def test_no_ecc_no_ecc_signals(self):
        spec = _make_mem_spec()
        ecc = _make_ecc_params()
        itype = _make_interface_type()
        tiling = _calc_tiling(spec, ecc)

        result = gen_bypass_wrapper(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert "ecc_correctable" not in result
        assert "ecc_uncorrectable" not in result
        assert "ecc_en" not in result

    def test_bypass_depth_in_parameter(self):
        spec = _make_mem_spec(
            input_pipe=2, ram_rd_latency=1, ecc_pipe=1, output_pipe=1,
        )
        ecc = _make_ecc_params()
        itype = _make_interface_type()
        tiling = _calc_tiling(spec, ecc)

        result = gen_bypass_wrapper(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert "BYPASS_DEPTH            = 5" in result

    def test_bypass_depth_zero_latency(self):
        spec = _make_mem_spec(
            input_pipe=0, ram_rd_latency=0, ecc_pipe=0, output_pipe=0,
        )
        ecc = _make_ecc_params()
        itype = _make_interface_type()
        tiling = _calc_tiling(spec, ecc)

        result = gen_bypass_wrapper(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert "BYPASS_DEPTH            = 0" in result


# ---------------------------------------------------------------------------
# Dispatch / error tests
# ---------------------------------------------------------------------------

class TestGenBypassWrapperDispatch:
    """Tests for gen_bypass_wrapper dispatch logic."""

    def test_unsupported_base_type_raises(self):
        spec = _make_mem_spec()
        ecc = _make_ecc_params()
        tiling = _calc_tiling(spec, ecc)
        itype = InterfaceType(
            base_type="single_port",
            has_mask=False,
            is_async=False,
            port_map={},
            sub_types=(),
        )

        with pytest.raises(ValueError, match="not supported for base_type"):
            gen_bypass_wrapper(spec, ecc, itype, "test_top", "test_mem", tiling)


# ---------------------------------------------------------------------------
# Config validation tests (enable_l3 constraints)
# ---------------------------------------------------------------------------

class TestEnableL3Validation:
    """Tests for enable_l3 validation in config_io."""

    def test_l3_without_l2_raises(self):
        from config_io import ConfigLoader

        loader = ConfigLoader()
        # Minimal mock — we only test _validate_memory
        mock_mem = {
            "name": "bad",
            "type": "1r1w",
            "width": 32,
            "depth": 256,
            "ecc": {"enable": False},
            "physical": {
                "sub_type": "uhd2prf",
                "lib_name": "X",
                "lib_width": 32,
                "lib_depth": 256,
            },
            "ram_rd_latency": 1,
            "input_pipe_stages": 0,
            "output_pipe_stages": 0,
            "enable_l2": False,
            "enable_l3": True,
        }
        itype = _make_interface_type()
        from config_io import VendorPortMap
        vpm = VendorPortMap(
            vendor="test", lib_paths=(),
            interface_types={"1r1w": itype},
        )

        with pytest.raises(ValueError, match="enable_l3=true requires enable_l2=true"):
            loader._validate_memory(mock_mem, vpm)

    def test_l3_on_async_type_raises(self):
        from config_io import ConfigLoader, VendorPortMap

        loader = ConfigLoader()
        async_itype = InterfaceType(
            base_type="dual_port",
            has_mask=False,
            is_async=True,
            port_map={
                "wr_clk": "CLKA", "wr_en": "~WEB", "wr_addr": "AA",
                "wr_data": "D", "rd_clk": "CLKB", "rd_en": "~REB",
                "rd_addr": "AB", "rd_data": "Q",
            },
            sub_types=(
                SubTypeInfo(names=("2prf",), const_ports={}, output_ports=()),
            ),
        )
        vpm = VendorPortMap(
            vendor="test", lib_paths=(),
            interface_types={"1r1wa": async_itype},
        )
        mock_mem = {
            "name": "bad_async",
            "type": "1r1wa",
            "width": 32,
            "depth": 256,
            "ecc": {"enable": False},
            "physical": {
                "sub_type": "2prf",
                "lib_name": "X",
                "lib_width": 32,
                "lib_depth": 256,
            },
            "ram_rd_latency": 1,
            "input_pipe_stages": 0,
            "output_pipe_stages": 0,
            "enable_l3": True,
        }

        with pytest.raises(ValueError, match="only supported for sync dual_port"):
            loader._validate_memory(mock_mem, vpm)

    def test_l3_on_single_port_raises(self):
        from config_io import ConfigLoader, VendorPortMap

        loader = ConfigLoader()
        sp_itype = InterfaceType(
            base_type="single_port",
            has_mask=False,
            is_async=False,
            port_map={"clk": "CLK", "cen": "~CEB", "wen": "~WEB",
                       "addr": "A", "wdata": "D", "rdata": "Q"},
            sub_types=(
                SubTypeInfo(names=("1prf",), const_ports={}, output_ports=()),
            ),
        )
        vpm = VendorPortMap(
            vendor="test", lib_paths=(),
            interface_types={"1rw": sp_itype},
        )
        mock_mem = {
            "name": "bad_sp",
            "type": "1rw",
            "width": 32,
            "depth": 256,
            "ecc": {"enable": False},
            "physical": {
                "sub_type": "1prf",
                "lib_name": "X",
                "lib_width": 32,
                "lib_depth": 256,
            },
            "ram_rd_latency": 1,
            "input_pipe_stages": 0,
            "output_pipe_stages": 0,
            "enable_l3": True,
        }

        with pytest.raises(ValueError, match="only supported for sync dual_port"):
            loader._validate_memory(mock_mem, vpm)


# ---------------------------------------------------------------------------
# Module naming tests
# ---------------------------------------------------------------------------

class TestComputeModuleNames:
    """Tests for _compute_module_names with enable_l3."""

    def test_l1_only(self):
        from mem_gen import _compute_module_names

        names = _compute_module_names("foo", enable_l2=False, enable_l3=False)
        assert names == {
            "l1_module": "foo_top",
            "l2_module": None,
            "l3_module": None,
        }

    def test_l1_l2(self):
        from mem_gen import _compute_module_names

        names = _compute_module_names("foo", enable_l2=True, enable_l3=False)
        assert names == {
            "l1_module": "foo_phy",
            "l2_module": "foo_top",
            "l3_module": None,
        }

    def test_l1_l2_l3(self):
        from mem_gen import _compute_module_names

        names = _compute_module_names("foo", enable_l2=True, enable_l3=True)
        assert names == {
            "l1_module": "foo_phy",
            "l2_module": "foo_mem",
            "l3_module": "foo_top",
        }


# ---------------------------------------------------------------------------
# Coarse-mask (mask_gran > 1) tests
# ---------------------------------------------------------------------------

class TestCoarseMaskBypass:
    """Tests for bypass wrapper with mask_gran > 1 (byte-mask etc.)."""

    def test_context_coarse_mask_width(self):
        """mask_width = width // mask_gran, bypass_data_width adjusted."""
        gen = DualPortBypassGen()
        # lib_width=32, lib_mask_width=4 → mask_gran=8
        spec = _make_mem_spec(type_="1r1wm", width=32, lib_width=32,
                              lib_mask_width=4)
        ecc = _make_ecc_params()
        itype = _make_interface_type(has_mask=True)
        tiling = _calc_tiling(spec, ecc)

        ctx = gen.build_context(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert ctx["mask_width"] == 4  # 32 / 8
        assert ctx["mask_gran"] == 8
        # bypass_data_width = 1 + 32 (data) + 4 (mask)
        assert ctx["bypass_data_width"] == 37

    def test_verilog_coarse_mask_localparams(self):
        """MASK_WIDTH / MASK_GRAN localparams present in output."""
        spec = _make_mem_spec(type_="1r1wm", width=32, lib_width=32,
                              lib_mask_width=4)
        ecc = _make_ecc_params()
        itype = _make_interface_type(has_mask=True)
        tiling = _calc_tiling(spec, ecc)

        v = gen_bypass_wrapper(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert "localparam MASK_WIDTH = 4;" in v
        assert "localparam MASK_GRAN  = 8;" in v

    def test_verilog_coarse_mask_expansion(self):
        """mask_gran > 1 generates G_BYP_MASK expansion block."""
        spec = _make_mem_spec(type_="1r1wm", width=32, lib_width=32,
                              lib_mask_width=4)
        ecc = _make_ecc_params()
        itype = _make_interface_type(has_mask=True)
        tiling = _calc_tiling(spec, ecc)

        v = gen_bypass_wrapper(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert "expanded_bwen" in v
        assert "G_BYP_MASK" in v
        assert "MASK_GRAN{delayed_bwen[g_byp]}" in v
        # Output mux uses expanded_bwen, not delayed_bwen
        assert "(delayed_wdata & expanded_bwen)" in v
        assert "(l2_rd_data & ~expanded_bwen)" in v

    def test_verilog_coarse_mask_unpack(self):
        """Unpack uses MASK_WIDTH for bwen slice."""
        spec = _make_mem_spec(type_="1r1wm", width=32, lib_width=32,
                              lib_mask_width=4)
        ecc = _make_ecc_params()
        itype = _make_interface_type(has_mask=True)
        tiling = _calc_tiling(spec, ecc)

        v = gen_bypass_wrapper(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert "bypass_entry_out[MASK_WIDTH + DATA_WIDTH]" in v
        assert "[DATA_WIDTH +: MASK_WIDTH]" in v
        assert "[MASK_WIDTH-1:0] delayed_bwen" in v

    def test_verilog_coarse_mask_port_width(self):
        """i_wr_bwen port uses literal mask_width, not DATA_WIDTH."""
        spec = _make_mem_spec(type_="1r1wm", width=32, lib_width=32,
                              lib_mask_width=4)
        ecc = _make_ecc_params()
        itype = _make_interface_type(has_mask=True)
        tiling = _calc_tiling(spec, ecc)

        v = gen_bypass_wrapper(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert "[4-1:0]" in v  # mask port width = 4
        assert "i_wr_bwen" in v

    def test_bit_level_mask_no_expansion(self):
        """mask_gran == 1 → no G_BYP_MASK expansion, uses direct AND/OR."""
        spec = _make_mem_spec(type_="1r1wm", width=32, lib_width=32,
                              lib_mask_width=32)
        ecc = _make_ecc_params()
        itype = _make_interface_type(has_mask=True)
        tiling = _calc_tiling(spec, ecc)

        v = gen_bypass_wrapper(spec, ecc, itype, "test_top", "test_mem", tiling)

        assert "G_BYP_MASK" not in v
        assert "expanded_bwen" not in v
        assert "(delayed_wdata & delayed_bwen)" in v
        assert "localparam MASK_GRAN  = 1;" in v


class TestGenBypassDefense:
    """Defensive behavior of gen_bypass_wrapper entry point."""

    def test_async_itype_raises(self):
        """Async dual_port raises ValueError — bypass only supports sync."""
        spec = _make_mem_spec(type_="1r1w")
        ecc = _make_ecc_params()
        async_itype = InterfaceType(
            base_type="dual_port",
            has_mask=False,
            is_async=True,
            port_map={},
            sub_types=(),
        )
        tiling = _calc_tiling(spec, ecc)
        with pytest.raises(ValueError, match="async dual_port is not supported"):
            gen_bypass_wrapper(spec, ecc, async_itype, "m", "l2", tiling)

    def test_bad_basetype_raises(self):
        """Unsupported base_type raises ValueError."""
        spec = _make_mem_spec()
        ecc = _make_ecc_params()
        bad_itype = InterfaceType(
            base_type="single_port",
            has_mask=False,
            is_async=False,
            port_map={},
            sub_types=(),
        )
        tiling = _calc_tiling(spec, ecc)
        with pytest.raises(ValueError, match="Only sync dual_port"):
            gen_bypass_wrapper(spec, ecc, bad_itype, "m", "l2", tiling)
