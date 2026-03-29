"""Tests for TB generation — tb_gen.py and tb_verilog.py.

Covers M7 verification plan: coarse mask vector generation, mask_expect
expansion, TB Verilog signal widths, and stimulus file correctness.
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config_io import InterfaceType, SubTypeInfo
from tb_gen import (
    _compute_mask_expect,
    _expand_mask,
    _fill_pattern,
    _format_hex,
    _generate_mask_vectors,
    _generate_write_data,
    gen_stimulus,
    gen_tb,
)
from helpers import make_ecc_params, make_mem_spec


# ---------------------------------------------------------------------------
# Lightweight InterfaceType fixtures
# ---------------------------------------------------------------------------

def _make_itype(base_type: str, has_mask: bool = False,
                is_async: bool = False) -> InterfaceType:
    return InterfaceType(
        base_type=base_type, has_mask=has_mask, is_async=is_async,
        port_map={}, sub_types=(
            SubTypeInfo(names=("test",), const_ports={}, output_ports=()),
        ),
    )


# ---------------------------------------------------------------------------
# _expand_mask tests
# ---------------------------------------------------------------------------

class TestExpandMask:
    """Coarse mask → bit-level expansion helper."""

    def test_gran1_identity(self):
        """mask_gran=1: no expansion, output == input."""
        assert _expand_mask(0b1010, 4, 1) == 0b1010

    def test_byte_mask_all_ones(self):
        """4-bit byte-mask all-1 → 32 bits all-1."""
        result = _expand_mask(0xF, 4, 8)
        assert result == 0xFFFFFFFF

    def test_byte_mask_alternating(self):
        """4-bit mask 0b1010 → each bit expands to 8 bits."""
        result = _expand_mask(0b1010, 4, 8)
        expected = 0xFF00FF00  # bit1→bits[15:8]=FF, bit3→bits[31:24]=FF
        assert result == expected

    def test_byte_mask_low_only(self):
        """4-bit mask 0b0001 → only lowest byte set."""
        result = _expand_mask(0b0001, 4, 8)
        assert result == 0x000000FF

    def test_nibble_mask(self):
        """8-bit nibble-mask (gran=4) → 32-bit expansion."""
        result = _expand_mask(0b10101010, 8, 4)
        expected = 0xF0F0F0F0
        assert result == expected

    def test_word_mask(self):
        """2-bit word-mask (gran=16) → 32-bit expansion."""
        result = _expand_mask(0b01, 2, 16)
        assert result == 0x0000FFFF


# ---------------------------------------------------------------------------
# _compute_mask_expect tests
# ---------------------------------------------------------------------------

class TestComputeMaskExpect:
    """Expected data after masked write — with coarse mask expansion."""

    def test_bit_level_no_expansion(self):
        """mask_gran=1: old behavior preserved."""
        old = [0xAAAAAAAA]
        new = [0x33333333]
        masks = [0xFFFF0000]
        result = _compute_mask_expect(old, new, masks, 32)
        # high 16 bits from new, low 16 from old
        assert result == [0x3333AAAA]

    def test_byte_mask_all_write(self):
        """Coarse mask all-1: all data from new."""
        old = [0xAAAAAAAA]
        new = [0x33333333]
        masks = [0xF]  # 4-bit mask, all 1
        result = _compute_mask_expect(old, new, masks, 32,
                                      mask_gran=8, mask_width=4)
        assert result == [0x33333333]

    def test_byte_mask_all_hold(self):
        """Coarse mask all-0: all data from old."""
        old = [0xAAAAAAAA]
        new = [0x33333333]
        masks = [0x0]
        result = _compute_mask_expect(old, new, masks, 32,
                                      mask_gran=8, mask_width=4)
        assert result == [0xAAAAAAAA]

    def test_byte_mask_partial(self):
        """Coarse mask 0b0101: bytes 0,2 from new, bytes 1,3 from old."""
        old = [0xAAAAAAAA]
        new = [0x33333333]
        masks = [0b0101]  # byte0 and byte2 written
        result = _compute_mask_expect(old, new, masks, 32,
                                      mask_gran=8, mask_width=4)
        # byte0=0x33, byte1=0xAA, byte2=0x33, byte3=0xAA
        assert result == [0xAA33AA33]


# ---------------------------------------------------------------------------
# Mask vector generation tests
# ---------------------------------------------------------------------------

class TestGenerateMaskVectors:
    """Mask vector generation at correct width."""

    def test_bit_level_width(self):
        """mask_gran=1: vectors at DATA_WIDTH."""
        vectors = _generate_mask_vectors(32)
        assert len(vectors) == 8
        for v in vectors:
            assert v < (1 << 32)

    def test_coarse_mask_width(self):
        """Vectors generated at mask_width, not data_width."""
        # mask_width=4 (byte-mask for 32-bit data)
        vectors = _generate_mask_vectors(4)
        assert len(vectors) == 8
        for v in vectors:
            assert v < (1 << 4)


# ---------------------------------------------------------------------------
# gen_stimulus tests
# ---------------------------------------------------------------------------

class TestGenStimulus:
    """Stimulus hex file generation."""

    def test_mask_hex_at_mask_width(self):
        """Mask hex file uses mask_width for formatting."""
        mem = make_mem_spec(type_="1rwm", width=32, lib_width=32,
                            lib_mask_width=4)
        itype = _make_itype("single_port", has_mask=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            files = gen_stimulus(mem, itype, "test", Path(tmpdir))

            assert "mask" in files
            mask_content = files["mask"].read_text(encoding="utf-8")
            # mask_width=4 → 1 hex digit per line
            data_lines = [l for l in mask_content.strip().split("\n")
                          if not l.startswith("//")]
            for line in data_lines:
                assert len(line) == 1  # ceil(4/4) = 1 hex digit

    def test_mask_expect_at_data_width(self):
        """mask_expect hex file uses DATA_WIDTH for formatting."""
        mem = make_mem_spec(type_="1rwm", width=32, lib_width=32,
                            lib_mask_width=4)
        itype = _make_itype("single_port", has_mask=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            files = gen_stimulus(mem, itype, "test", Path(tmpdir))

            assert "mask_expect" in files
            me_content = files["mask_expect"].read_text(encoding="utf-8")
            data_lines = [l for l in me_content.strip().split("\n")
                          if not l.startswith("//")]
            for line in data_lines:
                assert len(line) == 8  # ceil(32/4) = 8 hex digits

    def test_no_mask_no_mask_files(self):
        """Non-mask type: no mask/mask_expect files."""
        mem = make_mem_spec(type_="1rw", width=32)
        itype = _make_itype("single_port", has_mask=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            files = gen_stimulus(mem, itype, "test", Path(tmpdir))

            assert "mask" not in files
            assert "mask_expect" not in files

    def test_bit_level_mask_backward_compat(self):
        """mask_gran=1: mask hex width = data_width (same as before)."""
        mem = make_mem_spec(type_="1rwm", width=32, lib_width=32,
                            lib_mask_width=32)
        itype = _make_itype("single_port", has_mask=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            files = gen_stimulus(mem, itype, "test", Path(tmpdir))

            mask_content = files["mask"].read_text(encoding="utf-8")
            data_lines = [l for l in mask_content.strip().split("\n")
                          if not l.startswith("//")]
            for line in data_lines:
                assert len(line) == 8  # ceil(32/4)


# ---------------------------------------------------------------------------
# TB Verilog signal width tests
# ---------------------------------------------------------------------------

class TestTbVerilogMaskWidth:
    """TB Verilog output — mask signal declarations use mask_width."""

    def test_sp_mask_signal_width(self):
        """SP TB: bwen declared at mask_width."""
        mem = make_mem_spec(type_="1rwm", width=32, lib_width=32,
                            lib_mask_width=4)
        itype = _make_itype("single_port", has_mask=True)
        ecc = make_ecc_params(32, enabled=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            sim_dir = Path(tmpdir) / "sim"
            gen_tb(mem, ecc, itype, "test_sp", "test_sp_phy",
                   tb_dir, sim_dir)

            tb_text = (tb_dir / "tb_test_sp.v").read_text(encoding="utf-8")
            # mask_width = 32/8 = 4
            assert "MASK_WIDTH        = 4;" in tb_text
            assert "[MASK_WIDTH-1:0] mask_mem" in tb_text
            assert "[DATA_WIDTH-1:0] mask_expect_mem" in tb_text
            assert "[4-1:0]" in tb_text  # bwen signal

    def test_dp_mask_signal_width(self):
        """DP TB: wr_bwen declared at mask_width."""
        mem = make_mem_spec(type_="1r1wm", width=32, lib_width=32,
                            lib_mask_width=4)
        itype = _make_itype("dual_port", has_mask=True)
        ecc = make_ecc_params(32, enabled=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            sim_dir = Path(tmpdir) / "sim"
            gen_tb(mem, ecc, itype, "test_dp", "test_dp_phy",
                   tb_dir, sim_dir)

            tb_text = (tb_dir / "tb_test_dp.v").read_text(encoding="utf-8")
            assert "MASK_WIDTH        = 4;" in tb_text
            assert "[4-1:0]" in tb_text

    def test_tdp_mask_signal_width(self):
        """TDP TB: a_bwen/b_bwen declared at mask_width."""
        mem = make_mem_spec(type_="2rwm", width=32, lib_width=32,
                            lib_mask_width=4)
        itype = _make_itype("true_dual_port", has_mask=True)
        ecc = make_ecc_params(32, enabled=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            sim_dir = Path(tmpdir) / "sim"
            gen_tb(mem, ecc, itype, "test_tdp", "test_tdp_phy",
                   tb_dir, sim_dir)

            tb_text = (tb_dir / "tb_test_tdp.v").read_text(encoding="utf-8")
            assert "MASK_WIDTH        = 4;" in tb_text
            assert "[4-1:0]" in tb_text

    def test_bit_level_mask_backward_compat(self):
        """mask_gran=1: MASK_WIDTH == DATA_WIDTH."""
        mem = make_mem_spec(type_="1rwm", width=32, lib_width=32,
                            lib_mask_width=32)
        itype = _make_itype("single_port", has_mask=True)
        ecc = make_ecc_params(32, enabled=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            sim_dir = Path(tmpdir) / "sim"
            gen_tb(mem, ecc, itype, "test_bw", "test_bw_phy",
                   tb_dir, sim_dir)

            tb_text = (tb_dir / "tb_test_bw.v").read_text(encoding="utf-8")
            assert "MASK_WIDTH        = 32;" in tb_text


# ---------------------------------------------------------------------------
# Burst + pipeline read pattern tests
# ---------------------------------------------------------------------------

class TestBurstPipelineRead:
    """read_check_phase uses burst-issue + pipeline-check pattern."""

    def _gen_tb_text(self, base_type: str, has_mask: bool = False) -> str:
        type_map = {
            "single_port": "1rw",
            "dual_port": "1r1w",
            "true_dual_port": "2rw",
        }
        mem = make_mem_spec(type_=type_map[base_type], width=32)
        itype = _make_itype(base_type, has_mask=has_mask)
        ecc = make_ecc_params(32, enabled=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            gen_tb(mem, ecc, itype, "t", "t_phy", tb_dir, Path(tmpdir) / "sim")
            return (tb_dir / "tb_t.v").read_text(encoding="utf-8")

    def test_sp_burst_loop_bound(self):
        """SP: read loop bound is NUM_READ_VECTORS + TOTAL_RD_LATENCY."""
        tb = self._gen_tb_text("single_port")
        assert "NUM_READ_VECTORS + TOTAL_RD_LATENCY" in tb

    def test_dp_burst_loop_bound(self):
        """DP: read loop bound is NUM_READ_VECTORS + TOTAL_RD_LATENCY."""
        tb = self._gen_tb_text("dual_port")
        assert "NUM_READ_VECTORS + TOTAL_RD_LATENCY" in tb

    def test_tdp_burst_loop_bound(self):
        """TDP: Port B read loop uses burst+pipeline bound."""
        tb = self._gen_tb_text("true_dual_port")
        assert "NUM_READ_VECTORS + TOTAL_RD_LATENCY" in tb

    def test_pipeline_check_guarded_by_latency(self):
        """check_rdata is guarded by i >= TOTAL_RD_LATENCY."""
        tb = self._gen_tb_text("single_port")
        assert "i >= TOTAL_RD_LATENCY" in tb
        assert "i - TOTAL_RD_LATENCY" in tb

    def test_no_per_iteration_repeat(self):
        """Old sequential pattern (repeat(TOTAL_RD_LATENCY) per iter) must be gone."""
        tb = self._gen_tb_text("single_port")
        assert "repeat(TOTAL_RD_LATENCY)" not in tb


# ---------------------------------------------------------------------------
# TDP B->A path tests
# ---------------------------------------------------------------------------

class TestTdpBtoAPath:
    """TDP TB includes Port B write + Port A read & check section."""

    def _gen_tdp_tb(self, has_mask: bool = False) -> tuple[str, Path]:
        mem = make_mem_spec(type_="2rw" if not has_mask else "2rwm",
                            width=32, lib_width=32,
                            lib_mask_width=32 if not has_mask else 4)
        itype = _make_itype("true_dual_port", has_mask=has_mask)
        ecc = make_ecc_params(32, enabled=False)
        import tempfile
        tmpdir = tempfile.mkdtemp()
        tb_dir = Path(tmpdir) / "tb"
        gen_tb(mem, ecc, itype, "tdp_test", "tdp_test_phy",
               tb_dir, Path(tmpdir) / "sim")
        return (tb_dir / "tb_tdp_test.v").read_text(encoding="utf-8"), tb_dir

    def test_b_write_phase_present(self):
        """TB contains Port B Write section."""
        tb, _ = self._gen_tdp_tb()
        assert "Port B Write" in tb

    def test_a_read_check_phase_present(self):
        """TB contains Port B Write -> Port A Read & Check section."""
        tb, _ = self._gen_tdp_tb()
        assert "Port B Write -> Port A Read & Check" in tb

    def test_b_wr_data_mem_declared(self):
        """b_wr_data_mem and b_rd_expect_mem are declared."""
        tb, _ = self._gen_tdp_tb()
        assert "b_wr_data_mem" in tb
        assert "b_rd_expect_mem" in tb

    def test_b_wr_hex_readmemh(self):
        """$readmemh calls for B data hex files present."""
        tb, _ = self._gen_tdp_tb()
        assert "tdp_test_b_wr_data.hex" in tb
        assert "tdp_test_b_rd_expect.hex" in tb

    def test_b_hex_files_generated(self):
        """gen_stimulus generates b_wr_data.hex and b_rd_expect.hex for TDP."""
        mem = make_mem_spec(type_="2rw", width=32)
        itype = _make_itype("true_dual_port", has_mask=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            files = gen_stimulus(mem, itype, "tdp", Path(tmpdir))
            assert "b_wr_data" in files
            assert "b_rd_expect" in files

    def test_b_wr_data_is_inverted(self):
        """b_wr_data values are bitwise-inverted wr_data values."""
        mem = make_mem_spec(type_="2rw", width=32)
        itype = _make_itype("true_dual_port", has_mask=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            files = gen_stimulus(mem, itype, "inv_test", Path(tmpdir))
            wr_lines = [
                l for l in files["wr_data"].read_text(encoding="utf-8").splitlines()
                if not l.startswith("//")
            ]
            b_lines = [
                l for l in files["b_wr_data"].read_text(encoding="utf-8").splitlines()
                if not l.startswith("//")
            ]
            assert len(wr_lines) == len(b_lines)
            mask32 = 0xFFFFFFFF
            for wr, bw in zip(wr_lines, b_lines):
                assert (int(wr, 16) ^ mask32) == int(bw, 16)

    def test_non_tdp_has_no_b_phase(self):
        """SP TB does not contain b_wr_data_mem."""
        mem = make_mem_spec(type_="1rw", width=32)
        itype = _make_itype("single_port", has_mask=False)
        ecc = make_ecc_params(32, enabled=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            gen_tb(mem, ecc, itype, "sp_test", "sp_test_phy",
                   tb_dir, Path(tmpdir) / "sim")
            tb = (tb_dir / "tb_sp_test.v").read_text(encoding="utf-8")
            assert "b_wr_data_mem" not in tb


# ---------------------------------------------------------------------------
# Makefile generation tests
# ---------------------------------------------------------------------------

class TestGenMakefile:
    """gen_makefile produces correct Makefile content."""

    def test_makefile_created(self):
        """gen_makefile writes Makefile to tb_outdir."""
        from tb_gen import gen_makefile
        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            gen_makefile(["inst_a", "inst_b"], tb_dir)
            assert (tb_dir / "Makefile").exists()

    def test_makefile_contains_verilator_root(self):
        """Makefile defines VERILATOR_ROOT."""
        from tb_gen import gen_makefile
        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            gen_makefile(["my_inst"], tb_dir)
            mk = (tb_dir / "Makefile").read_text(encoding="utf-8")
            assert "VERILATOR_ROOT" in mk

    def test_makefile_two_step_build(self):
        """Makefile contains Verilator two-step: verilate then make."""
        from tb_gen import gen_makefile
        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            gen_makefile(["my_inst"], tb_dir)
            mk = (tb_dir / "Makefile").read_text(encoding="utf-8")
            assert "$(VERILATOR)" in mk
            assert "$(MAKE_CMD)" in mk

    def test_makefile_python3_override(self):
        """Makefile passes PYTHON3 override to mingw32-make."""
        from tb_gen import gen_makefile
        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            gen_makefile(["my_inst"], tb_dir)
            mk = (tb_dir / "Makefile").read_text(encoding="utf-8")
            assert "PYTHON3" in mk

    def test_makefile_clean_target(self):
        """Makefile has a 'clean' target that removes sim artifacts."""
        from tb_gen import gen_makefile
        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            gen_makefile(["inst_x"], tb_dir)
            mk = (tb_dir / "Makefile").read_text(encoding="utf-8")
            assert "clean:" in mk
            assert "rm -rf" in mk

    def test_makefile_all_instances_listed(self):
        """All passed instances appear in Makefile sim target."""
        from tb_gen import gen_makefile
        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            gen_makefile(["alpha", "beta", "gamma"], tb_dir)
            mk = (tb_dir / "Makefile").read_text(encoding="utf-8")
            for name in ("alpha", "beta", "gamma"):
                assert name in mk

    def test_makefile_sim_dir_auto_create(self):
        """Makefile creates SIM_DIR via order-only prerequisite."""
        from tb_gen import gen_makefile
        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            gen_makefile(["inst_y"], tb_dir)
            mk = (tb_dir / "Makefile").read_text(encoding="utf-8")
            assert "$(SIM_DIR)" in mk
            assert "mkdir" in mk

    def test_empty_instance_list(self):
        """gen_makefile with empty list still writes a valid Makefile."""
        from tb_gen import gen_makefile
        with tempfile.TemporaryDirectory() as tmpdir:
            tb_dir = Path(tmpdir) / "tb"
            gen_makefile([], tb_dir)
            mk = (tb_dir / "Makefile").read_text(encoding="utf-8")
            assert "clean:" in mk
