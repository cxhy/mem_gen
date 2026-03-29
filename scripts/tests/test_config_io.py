"""Unit tests for config_io.py — _validate_memory() mask rules + TilingParams.mask_gran."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config_io import (
    ConfigLoader,
    InterfaceType,
    SubTypeInfo,
    TilingParams,
    VendorPortMap,
    resolve_sub_type_from_lib_name,
)
from physical_wrapper_gen import PhysicalWrapperGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vendor_port_map(has_mask: bool = True) -> VendorPortMap:
    """Build a minimal VendorPortMap fixture for validation testing."""
    sub_type = SubTypeInfo(names=("1prf",), const_ports={}, output_ports=())
    port_map: dict[str, str] = {
        "clk": "CLK", "cen": "~CEB", "wen": "~WEB",
        "addr": "A", "wdata": "D", "rdata": "Q",
    }
    if has_mask:
        port_map["bwen"] = "~BWEB"
    itype = InterfaceType(
        base_type="single_port",
        has_mask=has_mask,
        is_async=False,
        port_map=port_map,
        sub_types=(sub_type,),
    )
    type_key = "1rwm" if has_mask else "1rw"
    return VendorPortMap(
        vendor="test",
        lib_paths=(),
        interface_types={type_key: itype},
    )


def _base_mem(type_: str = "1rwm", width: int = 32,
              lib_width: int = 32, lib_mask_width: int = 32,
              ecc_enable: bool = False) -> dict:
    """Build a minimal valid memory config dict."""
    mem: dict = {
        "name": "test_mem",
        "type": type_,
        "width": width,
        "depth": 256,
        "ram_rd_latency": 1,
        "input_pipe_stages": 0,
        "output_pipe_stages": 0,
        "ecc": {"enable": False},
        "physical": {
            "sub_type": "1prf",
            "lib_name": "TESTLIB",
            "lib_width": lib_width,
            "lib_depth": 256,
            "lib_mask_width": lib_mask_width,
        },
    }
    if ecc_enable:
        mem["ecc"] = {
            "enable": True,
            "code_type": "hsiao",
            "data_bits_per_slice": 8,
            "ecc_bits_per_slice": 5,
        }
    return mem


def _validate(mem: dict, has_mask: bool = True) -> None:
    ConfigLoader()._validate_memory(mem, _make_vendor_port_map(has_mask))


# ---------------------------------------------------------------------------
# TestCalcTilingMaskGran — TilingParams.mask_gran field
# ---------------------------------------------------------------------------

class TestCalcTilingMaskGran:
    calc = staticmethod(PhysicalWrapperGenerator.calc_tiling)

    def test_no_mask_defaults_to_1(self):
        """Without lib_mask_width, mask_gran should be 1."""
        t = self.calc(32, 256, 32, 256)
        assert t.mask_gran == 1

    def test_bit_level_mask(self):
        """lib_mask_width == lib_width → mask_gran == 1 (bit-level)."""
        t = self.calc(32, 256, 32, 256, lib_mask_width=32)
        assert t.mask_gran == 1

    def test_byte_mask(self):
        """lib_mask_width = lib_width/4 → mask_gran == 4 (byte-level for 32-bit)."""
        t = self.calc(32, 256, 32, 256, lib_mask_width=8)
        assert t.mask_gran == 4

    def test_nibble_mask(self):
        """lib_mask_width = 2 for lib_width=8 → mask_gran == 4."""
        t = self.calc(8, 256, 8, 256, lib_mask_width=2)
        assert t.mask_gran == 4

    def test_word_mask(self):
        """lib_mask_width = 1 for lib_width=32 → mask_gran == 32."""
        t = self.calc(32, 256, 32, 256, lib_mask_width=1)
        assert t.mask_gran == 32

    def test_mask_gran_with_tiling(self):
        """mask_gran is per-cell, independent of col_count."""
        t = self.calc(64, 256, 32, 256, lib_mask_width=8)
        assert t.col_count == 2
        assert t.mask_gran == 4  # lib_width(32) / lib_mask_width(8)
        assert t.total_mask_width == 16  # 2 cols × 8 mask bits/col


# ---------------------------------------------------------------------------
# TestValidateMaskGranWidth — width % mask_gran == 0
# ---------------------------------------------------------------------------

class TestValidateMaskGranWidth:

    def test_bit_level_any_width_passes(self):
        """mask_gran=1: any positive width is valid."""
        _validate(_base_mem(width=48, lib_width=32, lib_mask_width=32))

    def test_divisible_passes(self):
        """width=32, mask_gran=4 (lib_width=32, lib_mask_width=8): 32 % 4 == 0."""
        _validate(_base_mem(width=32, lib_width=32, lib_mask_width=8))

    def test_divisible_multi_col_passes(self):
        """width=48, lib_width=32, mask_gran=4: 48 % 4 == 0."""
        _validate(_base_mem(width=48, lib_width=32, lib_mask_width=8))

    def test_not_divisible_raises(self):
        """width=33, mask_gran=4: 33 % 4 != 0 → ValueError."""
        mem = _base_mem(width=33, lib_width=32, lib_mask_width=8)
        with pytest.raises(ValueError, match="evenly divisible by mask_gran"):
            _validate(mem)

    def test_error_message_contains_details(self):
        """Error message should include width, mask_gran, lib_width, lib_mask_width."""
        mem = _base_mem(width=30, lib_width=32, lib_mask_width=8)
        with pytest.raises(ValueError) as exc_info:
            _validate(mem)
        msg = str(exc_info.value)
        assert "30" in msg
        assert "mask_gran" in msg

    def test_word_mask_non_divisible_raises(self):
        """width=31, mask_gran=32: 31 % 32 != 0."""
        mem = _base_mem(width=31, lib_width=32, lib_mask_width=1)
        with pytest.raises(ValueError, match="evenly divisible by mask_gran"):
            _validate(mem)

    def test_word_mask_exact_divisible_passes(self):
        """width=32, mask_gran=32 (lib_mask_width=1): 32 % 32 == 0."""
        _validate(_base_mem(width=32, lib_width=32, lib_mask_width=1))


# ---------------------------------------------------------------------------
# TestValidateEccMaskAlignment — slice_count % lib_mask_width == 0
# ---------------------------------------------------------------------------

class TestValidateEccMaskAlignment:
    """ECC + mask: ECC slice_count must be evenly divisible by lib_mask_width."""

    def _ecc_mem(self, width: int, lib_mask_width: int,
                 k: int = 8, m: int = 5) -> dict:
        """Build an ECC+mask mem dict. lib_width=32 (bit-level vendor)."""
        mem = _base_mem(width=width, lib_width=32, lib_mask_width=lib_mask_width)
        mem["ecc"] = {
            "enable": True,
            "code_type": "hsiao",
            "data_bits_per_slice": k,
            "ecc_bits_per_slice": m,
        }
        return mem

    def test_aligned_passes(self):
        """width=32, k=8 → slice_count=4; lib_mask_width=4 → 4%4==0."""
        _validate(self._ecc_mem(width=32, lib_mask_width=4, k=8))

    def test_one_mask_per_slice_passes(self):
        """width=32, k=8 → slice_count=4; lib_mask_width=4 → each mask bit = 1 slice."""
        _validate(self._ecc_mem(width=32, lib_mask_width=4, k=8))

    def test_multiple_slices_per_mask_passes(self):
        """width=32, k=8 → slice_count=4; lib_mask_width=2 → each mask bit = 2 slices."""
        _validate(self._ecc_mem(width=32, lib_mask_width=2, k=8))

    def test_not_aligned_raises(self):
        """width=24, k=8 → slice_count=3; lib_mask_width=4 → 3%4 != 0.

        Setup guarantees earlier checks pass:
        - lib_width(32) % lib_mask_width(4) = 0  ✓
        - width(24)    % mask_gran(8)       = 0  ✓
        - slice_count(3) % lib_mask_width(4) != 0 → ECC alignment error
        """
        mem = self._ecc_mem(width=24, lib_mask_width=4, k=8)
        with pytest.raises(ValueError, match="ECC slice_count"):
            _validate(mem)

    def test_error_message_contains_counts(self):
        """Error should mention slice_count and lib_mask_width values."""
        mem = self._ecc_mem(width=24, lib_mask_width=4, k=8)
        with pytest.raises(ValueError) as exc_info:
            _validate(mem)
        msg = str(exc_info.value)
        assert "ECC" in msg or "slice_count" in msg
        assert "4" in msg  # lib_mask_width value

    def test_no_ecc_skips_alignment_check(self):
        """Without ECC, a lib_mask_width that would fail ECC alignment is fine."""
        # width=32, mask_gran=8 (lib_mask_width=4): 32%8=0 → passes
        # If ECC were enabled: slice_count=ceil(32/16)=2, 2%4=2 ≠ 0 → would fail
        # But ECC is disabled, so only the width check runs → passes
        mem = _base_mem(width=32, lib_width=32, lib_mask_width=4)
        _validate(mem)  # should not raise

    def test_ecc_disabled_no_alignment_check(self):
        """ECC disabled: slice_count alignment is never checked."""
        # slice_count=ceil(32/8)=4, lib_mask_width=4: aligned, but that's irrelevant
        # The key point: no ECC alignment check fires
        mem = _base_mem(width=32, lib_width=32, lib_mask_width=4)
        _validate(mem)  # must pass without ECC alignment check

    def test_slice_count_ceiling(self):
        """Ceiling division: width=32, k=8 → slice_count=4; lib_mask_width=4 → passes."""
        _validate(self._ecc_mem(width=32, lib_mask_width=4, k=8))

    def test_slice_count_ceiling_fail(self):
        """width=24, k=8 → slice_count=ceil(24/8)=3; lib_mask_width=4 → 3%4 != 0."""
        mem = self._ecc_mem(width=24, lib_mask_width=4, k=8)
        with pytest.raises(ValueError, match="ECC slice_count"):
            _validate(mem)

    def test_bit_level_mask_skips_alignment(self):
        """mask_gran=1 (bit-level): ECC alignment check is skipped."""
        # lib_width=32, lib_mask_width=32 → mask_gran=1
        # slice_count=1, 1%32 ≠ 0, but mask_gran==1 so check skipped
        mem = _base_mem(width=32, lib_width=32, lib_mask_width=32)
        mem["ecc"] = {
            "enable": True,
            "code_type": "hsiao",
            "data_bits_per_slice": 102,
            "ecc_bits_per_slice": 8,
        }
        _validate(mem)


# ---------------------------------------------------------------------------
# TestResolveSubTypeFromLibName — lib_name → sub_type inference
# ---------------------------------------------------------------------------

_SAMPLE_MAP = {
    "ts5n7a": "1prf",
    "ts7n7a": "uhd1prf",
    "ts1n7sba": "spsbsram",
    "ts1n7uhda": "uhdspsram",
    "ts1n7mba": "spmbsram",
    "ts6n7a": "2prf",
    "ts6n7b": "uhd2prf",
    "tsdn7a": "dpsram",
    "ts5n7l1": "l1cache",
    "ts3n7a": "rom",
}
_STRIP = ("ulvt", "svt", "lvt")


class TestResolveSubTypeFromLibName:

    def test_basic_match(self):
        """Simple prefix match without suffixes."""
        result = resolve_sub_type_from_lib_name(
            "ts5n7a256x32", _SAMPLE_MAP, _STRIP)
        assert result == "1prf"

    def test_strip_ulvt_suffix(self):
        """lib_name with 'ulvt' voltage suffix stripped before match."""
        result = resolve_sub_type_from_lib_name(
            "ts5n7aulvt256x32", _SAMPLE_MAP, _STRIP)
        assert result == "1prf"

    def test_strip_svt_suffix(self):
        """lib_name with 'svt' voltage suffix stripped before match."""
        result = resolve_sub_type_from_lib_name(
            "ts6n7asvt128x64", _SAMPLE_MAP, _STRIP)
        assert result == "2prf"

    def test_strip_lvt_suffix(self):
        """lib_name with 'lvt' voltage suffix stripped before match."""
        result = resolve_sub_type_from_lib_name(
            "tsdn7alvt512x16", _SAMPLE_MAP, _STRIP)
        assert result == "dpsram"

    def test_case_insensitive(self):
        """Upper-case lib_name should match lower-case prefixes."""
        result = resolve_sub_type_from_lib_name(
            "TS5N7A256x32", _SAMPLE_MAP, _STRIP)
        assert result == "1prf"

    def test_longest_prefix_wins(self):
        """When multiple prefixes match, the longest one wins."""
        ambiguous_map = {"ts5n7": "short_match", "ts5n7a": "long_match"}
        result = resolve_sub_type_from_lib_name(
            "ts5n7a256x32", ambiguous_map, ())
        assert result == "long_match"

    def test_no_match_raises(self):
        """Unrecognized lib_name prefix raises ValueError."""
        with pytest.raises(ValueError, match="Cannot infer sub_type"):
            resolve_sub_type_from_lib_name(
                "UNKNOWN_SRAM_256x32", _SAMPLE_MAP, _STRIP)

    def test_empty_map_raises(self):
        """Empty lib_name_map always raises."""
        with pytest.raises(ValueError, match="Cannot infer sub_type"):
            resolve_sub_type_from_lib_name("ts5n7a256x32", {}, ())

    def test_rom_prefix(self):
        """ROM prefix ts3n7a should map correctly."""
        result = resolve_sub_type_from_lib_name(
            "ts3n7a1024x32", _SAMPLE_MAP, _STRIP)
        assert result == "rom"

    def test_uhd2prf_prefix(self):
        """ts6n7b should map to uhd2prf, not 2prf (ts6n7a)."""
        result = resolve_sub_type_from_lib_name(
            "ts6n7b128x64svt", _SAMPLE_MAP, _STRIP)
        assert result == "uhd2prf"


# ---------------------------------------------------------------------------
# TestSubTypeAutoInference — _validate_memory with optional sub_type
# ---------------------------------------------------------------------------

def _make_vendor_port_map_with_lib_map(has_mask: bool = True) -> VendorPortMap:
    """VendorPortMap with lib_name_map configured."""
    sub_type = SubTypeInfo(names=("1prf",), const_ports={}, output_ports=())
    port_map: dict[str, str] = {
        "clk": "CLK", "cen": "~CEB", "wen": "~WEB",
        "addr": "A", "wdata": "D", "rdata": "Q",
    }
    if has_mask:
        port_map["bwen"] = "~BWEB"
    itype = InterfaceType(
        base_type="single_port",
        has_mask=has_mask,
        is_async=False,
        port_map=port_map,
        sub_types=(sub_type,),
    )
    type_key = "1rwm" if has_mask else "1rw"
    return VendorPortMap(
        vendor="test",
        lib_paths=(),
        interface_types={type_key: itype},
        lib_name_map={"ts5n7a": "1prf"},
        lib_name_strip_suffixes=("ulvt", "svt", "lvt"),
    )


class TestSubTypeAutoInference:

    def test_explicit_sub_type_still_works(self):
        """When sub_type is provided explicitly, it is used as-is."""
        mem = _base_mem(type_="1rw", lib_mask_width=0)
        vpm = _make_vendor_port_map_with_lib_map(has_mask=False)
        ConfigLoader()._validate_memory(mem, vpm)
        assert mem["physical"]["sub_type"] == "1prf"

    def test_infer_sub_type_from_lib_name(self):
        """When sub_type is omitted, it is inferred from lib_name."""
        mem = _base_mem(type_="1rw", lib_mask_width=0)
        del mem["physical"]["sub_type"]
        mem["physical"]["lib_name"] = "ts5n7a256x32"
        vpm = _make_vendor_port_map_with_lib_map(has_mask=False)
        ConfigLoader()._validate_memory(mem, vpm)
        assert mem["physical"]["sub_type"] == "1prf"

    def test_infer_sub_type_with_voltage_suffix(self):
        """Voltage suffix stripped before prefix match."""
        mem = _base_mem(type_="1rw", lib_mask_width=0)
        del mem["physical"]["sub_type"]
        mem["physical"]["lib_name"] = "ts5n7aulvt256x32"
        vpm = _make_vendor_port_map_with_lib_map(has_mask=False)
        ConfigLoader()._validate_memory(mem, vpm)
        assert mem["physical"]["sub_type"] == "1prf"

    def test_missing_sub_type_no_map_raises(self):
        """Without lib_name_map, missing sub_type raises ValueError."""
        mem = _base_mem(type_="1rw", lib_mask_width=0)
        del mem["physical"]["sub_type"]
        vpm = _make_vendor_port_map(has_mask=False)  # no lib_name_map
        with pytest.raises(ValueError, match="sub_type is missing"):
            ConfigLoader()._validate_memory(mem, vpm)

    def test_missing_sub_type_bad_lib_name_raises(self):
        """sub_type omitted + unrecognized lib_name raises ValueError."""
        mem = _base_mem(type_="1rw", lib_mask_width=0)
        del mem["physical"]["sub_type"]
        mem["physical"]["lib_name"] = "UNKNOWN_SRAM_256x32"
        vpm = _make_vendor_port_map_with_lib_map(has_mask=False)
        with pytest.raises(ValueError, match="Cannot infer sub_type"):
            ConfigLoader()._validate_memory(mem, vpm)


class TestValidateNonNegativeStages:
    """_validate_memory rejects negative latency/pipe-stage values."""

    def test_negative_ram_rd_latency_raises(self):
        mem = _base_mem(type_="1rw", lib_mask_width=0)
        mem["ram_rd_latency"] = -1
        with pytest.raises(ValueError, match="ram_rd_latency"):
            _validate(mem, has_mask=False)

    def test_negative_input_pipe_raises(self):
        mem = _base_mem(type_="1rw", lib_mask_width=0)
        mem["input_pipe_stages"] = -2
        with pytest.raises(ValueError, match="input_pipe_stages"):
            _validate(mem, has_mask=False)

    def test_negative_output_pipe_raises(self):
        mem = _base_mem(type_="1rw", lib_mask_width=0)
        mem["output_pipe_stages"] = -1
        with pytest.raises(ValueError, match="output_pipe_stages"):
            _validate(mem, has_mask=False)

    def test_negative_ecc_pipe_raises(self):
        mem = _base_mem(type_="1rw", lib_mask_width=0)
        mem["ecc_pipe_stages"] = -1
        with pytest.raises(ValueError, match="ecc_pipe_stages"):
            _validate(mem, has_mask=False)

    def test_zero_stages_valid(self):
        """Zero is valid for all stage fields."""
        mem = _base_mem(type_="1rw", lib_mask_width=0)
        mem["ram_rd_latency"] = 1
        mem["input_pipe_stages"] = 0
        mem["output_pipe_stages"] = 0
        mem["ecc_pipe_stages"] = 0
        _validate(mem, has_mask=False)  # should not raise
