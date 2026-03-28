"""Testbench generator — produces self-checking TB, hex stimulus, and sim scripts.

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

from config_io import EccParams, InterfaceType, MemorySpec
from verilog_utils import clog2
import secded_gen
import tb_verilog

# ---------------------------------------------------------------------------
# Jinja2 environment
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
    undefined=jinja2.StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)

_tb_tmpl = _env.get_template("tb.v.j2")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_VECTORS = 32
_NUM_MASK_VECTORS = 8

# Fixed data patterns (hex nibbles, repeated to fill width)
_PATTERNS = [0xA5, 0x5A, 0xDE, 0xCA]
_PATTERN_LABELS = ["A5A5", "5A5A", "DEAD", "CAFE"]

# Mask patterns: 8 fixed mask vectors (2-byte patterns for proper alternation)
_MASK_PATTERNS = [
    0xFFFF,  # 全写入
    0x0000,  # 全保持
    0xFF00,  # 高字节写入
    0x00FF,  # 低字节写入
    0xAAAA,  # 交替位写入
    0x5555,  # 交替位写入 (反相)
    0x0F0F,  # Nibble 交替
    0xF0F0,  # Nibble 交替 (反相)
]

# New data for mask write phase — use inverted patterns
_MASK_NEW_DATA_BYTE = 0x33


# ---------------------------------------------------------------------------
# Hex data generation helpers
# ---------------------------------------------------------------------------

def _fill_pattern(byte_val: int, width: int) -> int:
    """Repeat byte_val across width bits."""
    byte_count = math.ceil(width / 8)
    raw = 0
    for i in range(byte_count):
        raw |= byte_val << (i * 8)
    mask = (1 << width) - 1
    return raw & mask


def _addr_based_pattern(addr: int, width: int) -> int:
    """Build {addr, ~addr, addr, ~addr, ...} filling width bits."""
    addr_w = max(addr.bit_length(), 1)
    addr_mask = (1 << addr_w) - 1
    parts = [addr & addr_mask, (~addr) & addr_mask]
    raw = 0
    bit_pos = 0
    idx = 0
    while bit_pos < width:
        chunk = parts[idx % 2]
        raw |= chunk << bit_pos
        bit_pos += addr_w
        idx += 1
    return raw & ((1 << width) - 1)


def _generate_write_data(width: int, num_vectors: int) -> list[int]:
    """Generate write data vectors per §4.3 strategy."""
    data: list[int] = []
    all_ones = (1 << width) - 1
    for i in range(num_vectors):
        if i == 0:
            data.append(_fill_pattern(0xA5, width))
        elif i == 1:
            data.append(_fill_pattern(0x5A, width))
        elif i == 2:
            data.append(_fill_pattern(0xDE, width))
        elif i == 3:
            data.append(_fill_pattern(0xCA, width))
        elif i == num_vectors - 2:
            data.append(all_ones)
        elif i == num_vectors - 1:
            data.append(0)
        else:
            data.append(_addr_based_pattern(i, width))
    return data


def _generate_mask_vectors(width: int) -> list[int]:
    """Generate 8 mask vectors per §4.4."""
    result: list[int] = []
    for pat16 in _MASK_PATTERNS:
        result.append(_fill_pattern_16(pat16, width))
    return result


def _fill_pattern_16(val16: int, width: int) -> int:
    """Repeat a 16-bit pattern across width bits."""
    chunk_count = math.ceil(width / 16)
    raw = 0
    for i in range(chunk_count):
        raw |= (val16 & 0xFFFF) << (i * 16)
    return raw & ((1 << width) - 1)


def _compute_mask_expect(
    old_data: list[int],
    new_data: list[int],
    masks: list[int],
    width: int,
    mask_gran: int = 1,
    mask_width: int | None = None,
) -> list[int]:
    """expected[i] = (old_data[i] & ~expanded_mask[i]) | (new_data[i] & expanded_mask[i]).

    When mask_gran > 1, each coarse mask bit is expanded to control
    mask_gran consecutive data bits before applying.
    """
    if mask_width is None:
        mask_width = width
    all_ones = (1 << width) - 1
    result: list[int] = []
    for i in range(len(masks)):
        m = _expand_mask(masks[i], mask_width, mask_gran) if mask_gran > 1 else masks[i]
        old = old_data[i] if i < len(old_data) else 0
        new = new_data[i] if i < len(new_data) else 0
        expected = (old & (m ^ all_ones)) | (new & m)
        result.append(expected)
    return result


def _expand_mask(mask: int, mask_width: int, mask_gran: int) -> int:
    """Expand coarse mask to bit-level: each mask bit replicates mask_gran times."""
    result = 0
    gran_mask = (1 << mask_gran) - 1
    for i in range(mask_width):
        if mask & (1 << i):
            result |= gran_mask << (i * mask_gran)
    return result


def _calc_mask_params(mem_spec: MemorySpec, has_mask: bool) -> tuple[int, int]:
    """Return (mask_gran, mask_width) for mask port sizing."""
    if not has_mask:
        return 1, mem_spec.width
    lib_mw = mem_spec.physical.lib_mask_width
    mask_gran = mem_spec.physical.lib_width // lib_mw if lib_mw > 0 else 1
    mask_width = mem_spec.width // mask_gran
    return mask_gran, mask_width


def _format_hex(value: int, width: int) -> str:
    """Format value as hex string with correct number of hex digits."""
    hex_digits = math.ceil(width / 4)
    return f"{value:0{hex_digits}X}"


def _write_hex_file(path: Path, values: list[int], width: int,
                    top_name: str, description: str) -> None:
    """Write a $readmemh compatible hex file."""
    lines = [
        f"// {top_name}_{description}",
        "// Generated by sram_mem_gen — DO NOT EDIT",
    ]
    for v in values:
        lines.append(_format_hex(v, width))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# ECC encode helper (for ROM init data pre-encoding)
# ---------------------------------------------------------------------------

def _ecc_encode_word(data: int, k: int, m: int,
                     enc_masks: tuple[int, ...]) -> int:
    """Encode a k-bit data word into an n-bit codeword (k data + m check).

    Replicates the RTL encoder logic: each check bit is the XOR-parity
    of all data bits selected by the corresponding enc_mask.
    """
    check_bits = 0
    for j in range(m):
        parity = bin(data & enc_masks[j]).count("1") % 2
        check_bits |= parity << j
    return data | (check_bits << k)


def _ecc_encode_rom_data(
    raw_data: list[int],
    ecc_params: EccParams,
    mem_spec: MemorySpec,
) -> list[int]:
    """Encode raw data words for ROM+ECC: pad to k, encode per slice, assemble.

    Returns list of data_with_ecc_width-bit encoded words.
    """
    ecc_cfg = mem_spec.ecc
    k = ecc_params.k
    m = ecc_params.m
    slice_count = ecc_params.slice_count
    n = k + m

    codes = secded_gen.gen_code(ecc_cfg.code_type, k, m, seed=ecc_cfg.seed)
    enc_masks = secded_gen.calc_bitmasks(k, m, codes, dec=False)

    k_mask = (1 << k) - 1
    encoded: list[int] = []
    for raw in raw_data:
        # Pad raw data (design width) to data_pad_width by zero-extending
        padded = raw & ((1 << ecc_params.data_pad_width) - 1)
        # Encode each slice
        codeword = 0
        for s in range(slice_count):
            slice_data = (padded >> (s * k)) & k_mask
            slice_encoded = _ecc_encode_word(slice_data, k, m, enc_masks)
            codeword |= slice_encoded << (s * n)
        encoded.append(codeword)
    return encoded


# ---------------------------------------------------------------------------
# Stimulus generation (public)
# ---------------------------------------------------------------------------

def gen_stimulus(
    mem_spec: MemorySpec,
    interface_type: InterfaceType,
    top_name: str,
    tb_outdir: Path,
    ecc_params: EccParams | None = None,
) -> dict[str, Path]:
    """Generate hex stimulus files. Returns dict of file type → path."""
    width = mem_spec.width
    depth = mem_spec.depth
    num_vectors = min(depth, _MAX_VECTORS)
    has_mask = interface_type.has_mask
    is_rom = interface_type.base_type == "rom"
    has_ecc = (ecc_params is not None and ecc_params.enabled
               and mem_spec.enable_l2)

    tb_outdir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}

    # Write data / ROM init
    wr_data = _generate_write_data(width, num_vectors)

    if is_rom:
        if has_ecc:
            # Pre-encode with ECC check bits so decoder reads correct codewords
            encoded = _ecc_encode_rom_data(wr_data, ecc_params, mem_spec)
            rom_width = ecc_params.data_with_ecc_width
        else:
            encoded = wr_data
            rom_width = width
        rom_path = tb_outdir / f"{top_name}_rom_init.hex"
        _write_hex_file(rom_path, encoded, rom_width, top_name, "rom_init")
        files["rom_init"] = rom_path
    else:
        wr_path = tb_outdir / f"{top_name}_wr_data.hex"
        _write_hex_file(wr_path, wr_data, width, top_name, "wr_data")
        files["wr_data"] = wr_path

    # Read expect (same as write data for basic test)
    rd_path = tb_outdir / f"{top_name}_rd_expect.hex"
    _write_hex_file(rd_path, wr_data, width, top_name, "rd_expect")
    files["rd_expect"] = rd_path

    # Mask vectors
    if has_mask:
        mask_gran, mask_width = _calc_mask_params(mem_spec, has_mask)
        num_mask = min(_NUM_MASK_VECTORS, num_vectors)
        masks = _generate_mask_vectors(mask_width)[:num_mask]
        mask_path = tb_outdir / f"{top_name}_mask.hex"
        _write_hex_file(mask_path, masks, mask_width, top_name, "mask")
        files["mask"] = mask_path

        # New data for masked writes
        new_data = [_fill_pattern(_MASK_NEW_DATA_BYTE, width)] * num_mask
        # Old data = wr_data at those addresses
        old_data = wr_data[:num_mask]
        mask_expect = _compute_mask_expect(
            old_data, new_data, masks, width, mask_gran, mask_width,
        )
        me_path = tb_outdir / f"{top_name}_mask_expect.hex"
        _write_hex_file(me_path, mask_expect, width, top_name, "mask_expect")
        files["mask_expect"] = me_path

    return files


# ---------------------------------------------------------------------------
# Sim script generation (public)
# ---------------------------------------------------------------------------

def gen_sim_script(top_name: str, sim_outdir: Path) -> Path:
    """Generate run_{top_name}.sh. Returns script path."""
    sim_outdir.mkdir(parents=True, exist_ok=True)
    script_path = sim_outdir / f"run_{top_name}.sh"
    content = f"""\
#!/bin/bash
set -euo pipefail

# Configuration
TOP_NAME="{top_name}"
SIM_OUT="sim_${{TOP_NAME}}"
LOG_FILE="${{SIM_OUT}}.log"

# Paths
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Compiling ${{TOP_NAME}} ==="
pushd ../rtl > /dev/null
iverilog -g2012 -DSIM \\
    -f filelist.f \\
    -o "../sim/$SIM_OUT" \\
    "../tb/tb_${{TOP_NAME}}.v"
popd > /dev/null

echo "=== Running simulation ==="
pushd ../tb > /dev/null
vvp "../sim/$SIM_OUT" | tee "../sim/$LOG_FILE"
popd > /dev/null

echo "=== Checking result ==="
if grep -q "^PASS:" "$LOG_FILE"; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL"
    exit 1
fi
"""
    script_path.write_text(content, encoding="utf-8")
    return script_path


def gen_run_all_script(sim_outdir: Path) -> Path:
    """Generate run_all.sh regression script."""
    sim_outdir.mkdir(parents=True, exist_ok=True)
    script_path = sim_outdir / "run_all.sh"
    content = """\
#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PASS=0
FAIL=0
TOTAL=0

for script in run_*.sh; do
    [ "$script" = "run_all.sh" ] && continue
    TOTAL=$((TOTAL + 1))
    echo "--- Running $script ---"
    if bash "$script"; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
    fi
    echo ""
done

echo "========================================="
echo "Regression: $PASS/$TOTAL passed, $FAIL failed"
echo "========================================="

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
"""
    script_path.write_text(content, encoding="utf-8")
    return script_path


# ---------------------------------------------------------------------------
# TB Generator base class
# ---------------------------------------------------------------------------

class TbGenerator(ABC):
    """Base class for testbench context builders."""

    @abstractmethod
    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      interface_type: InterfaceType,
                      top_name: str, phy_wrapper_name: str) -> dict:
        ...

    def generate(self, mem_spec: MemorySpec, ecc_params: EccParams,
                 interface_type: InterfaceType,
                 top_name: str, phy_wrapper_name: str) -> str:
        ctx = self.build_context(mem_spec, ecc_params, interface_type,
                                 top_name, phy_wrapper_name)
        return _tb_tmpl.render(ctx)

    # -- Shared helpers -------------------------------------------------------

    @staticmethod
    def _base_context(mem_spec: MemorySpec, ecc_params: EccParams,
                      interface_type: InterfaceType,
                      top_name: str) -> dict:
        """Build scalar fields shared by all TB generators."""
        has_ecc = ecc_params.enabled and mem_spec.enable_l2
        has_mask = interface_type.has_mask
        is_async = interface_type.is_async
        is_l2 = mem_spec.enable_l2
        has_init = interface_type.base_type != "rom" and is_l2
        is_rom = interface_type.base_type == "rom"

        width = mem_spec.width
        depth = mem_spec.depth
        addr_width = clog2(depth)
        num_vectors = min(depth, _MAX_VECTORS)
        num_mask = min(_NUM_MASK_VECTORS, num_vectors) if has_mask else 0

        if is_l2:
            total_rd_latency = (
                mem_spec.input_pipe_stages
                + mem_spec.ram_rd_latency
                + mem_spec.ecc_pipe_stages
                + mem_spec.output_pipe_stages
            )
        else:
            total_rd_latency = mem_spec.ram_rd_latency

        ctx: dict = {
            "top_name": top_name,
            "description": f"Testbench for {mem_spec.name} ({mem_spec.type})",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "data_width": width,
            "ram_depth": depth,
            "addr_width": addr_width,
            "total_rd_latency": total_rd_latency,
            "has_ecc": has_ecc,
            "has_mask": has_mask,
            "has_init": has_init,
            "is_async": is_async,
            "is_l2": is_l2,
            "is_rom": is_rom,
            "base_type": interface_type.base_type,
            "num_write_vectors": num_vectors,
            "num_read_vectors": num_vectors,
            "num_mask_vectors": num_mask,
            "mask_count_expr": " + NUM_MASK_VECTORS" if has_mask else "",
            "mask_new_data_hex": _format_hex(
                _fill_pattern(_MASK_NEW_DATA_BYTE, width), width,
            ),
        }

        if has_ecc:
            ctx["ecc_m"] = ecc_params.m

        # Hex file names (relative — TB and hex are in same directory)
        ctx["wr_hex_file"] = f"{top_name}_wr_data.hex"
        ctx["rd_hex_file"] = f"{top_name}_rd_expect.hex"
        if is_rom:
            ctx["rom_init_hex"] = f"{top_name}_rom_init.hex"
        if has_mask:
            ctx["mask_hex_file"] = f"{top_name}_mask.hex"
            ctx["mask_expect_hex_file"] = f"{top_name}_mask_expect.hex"
            mask_gran, mask_width = _calc_mask_params(mem_spec, has_mask)
            ctx["mask_width"] = mask_width
            ctx["mask_gran"] = mask_gran

        return ctx


# ---------------------------------------------------------------------------
# SinglePortTbGen — 1rw, 1rwm
# ---------------------------------------------------------------------------

class SinglePortTbGen(TbGenerator):

    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      interface_type: InterfaceType,
                      top_name: str, phy_wrapper_name: str) -> dict:
        ctx = self._base_context(mem_spec, ecc_params, interface_type, top_name)
        is_l2 = ctx["is_l2"]
        has_ecc = ctx["has_ecc"]
        has_mask = ctx["has_mask"]
        p = "i_" if is_l2 else ""

        ctx["clock_decl"] = "reg clk = 0;"
        ctx["clock_gen"] = "always #5 clk = ~clk;  // 100MHz"
        ctx["main_clk"] = "clk"

        # DUT signal declarations
        decls = tb_verilog.sp_signal_decls(ctx, p)
        ctx["dut_signal_decls"] = "\n".join(decls)

        # DUT instance
        ctx["dut_instance"] = tb_verilog.sp_dut_instance(ctx, p, top_name)

        # Signal init
        inits = tb_verilog.sp_signal_inits(ctx, p)
        ctx["init_signals"] = "\n    ".join(inits)

        # Phase blocks
        ctx["init_phase"] = tb_verilog.sp_init_phase(p) if ctx["has_init"] else ""
        ctx["write_phase"] = tb_verilog.sp_write_phase(ctx, p)
        ctx["read_check_phase"] = tb_verilog.sp_read_check_phase(ctx, p)

        if has_mask:
            ctx["mask_write_phase"] = tb_verilog.sp_mask_write_phase(ctx, p)
            ctx["mask_read_check_phase"] = tb_verilog.sp_mask_read_check_phase(ctx, p)
        else:
            ctx["mask_write_phase"] = ""
            ctx["mask_read_check_phase"] = ""

        return ctx


# ---------------------------------------------------------------------------
# DualPortTbGen — 1r1w, 1r1wm, 1r1wa, 1r1wma
# ---------------------------------------------------------------------------

class DualPortTbGen(TbGenerator):

    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      interface_type: InterfaceType,
                      top_name: str, phy_wrapper_name: str) -> dict:
        ctx = self._base_context(mem_spec, ecc_params, interface_type, top_name)
        is_l2 = ctx["is_l2"]
        has_ecc = ctx["has_ecc"]
        has_mask = ctx["has_mask"]
        is_async = ctx["is_async"]
        p = "i_" if is_l2 else ""

        if is_async:
            ctx["clock_decl"] = "reg wr_clk = 0;\nreg rd_clk = 0;"
            ctx["clock_gen"] = (
                "always #5 wr_clk = ~wr_clk;  // 100MHz\n"
                "always #7 rd_clk = ~rd_clk;  // ~71MHz"
            )
            ctx["main_clk"] = "wr_clk"
            wr_clk = "wr_clk"
            rd_clk = "rd_clk"
        else:
            ctx["clock_decl"] = "reg clk = 0;"
            ctx["clock_gen"] = "always #5 clk = ~clk;  // 100MHz"
            ctx["main_clk"] = "clk"
            wr_clk = "clk"
            rd_clk = "clk"

        decls = tb_verilog.dp_signal_decls(ctx, p, is_async)
        ctx["dut_signal_decls"] = "\n".join(decls)

        ctx["dut_instance"] = tb_verilog.dp_dut_instance(
            ctx, p, top_name, is_async)

        inits = tb_verilog.dp_signal_inits(ctx, p)
        ctx["init_signals"] = "\n    ".join(inits)

        ctx["init_phase"] = tb_verilog.dp_init_phase(p, wr_clk) if ctx["has_init"] else ""
        ctx["write_phase"] = tb_verilog.dp_write_phase(ctx, p, wr_clk)
        ctx["read_check_phase"] = tb_verilog.dp_read_check_phase(
            ctx, p, rd_clk, is_async)

        if has_mask:
            ctx["mask_write_phase"] = tb_verilog.dp_mask_write_phase(ctx, p, wr_clk)
            ctx["mask_read_check_phase"] = tb_verilog.dp_mask_read_check_phase(
                ctx, p, rd_clk, is_async)
        else:
            ctx["mask_write_phase"] = ""
            ctx["mask_read_check_phase"] = ""

        return ctx


# ---------------------------------------------------------------------------
# TrueDualPortTbGen — 2rw, 2rwm
# ---------------------------------------------------------------------------

class TrueDualPortTbGen(TbGenerator):

    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      interface_type: InterfaceType,
                      top_name: str, phy_wrapper_name: str) -> dict:
        ctx = self._base_context(mem_spec, ecc_params, interface_type, top_name)
        is_l2 = ctx["is_l2"]
        has_ecc = ctx["has_ecc"]
        has_mask = ctx["has_mask"]
        p = "i_" if is_l2 else ""

        ctx["clock_decl"] = "reg a_clk = 0;\nreg b_clk = 0;"
        ctx["clock_gen"] = (
            "always #5 a_clk = ~a_clk;  // 100MHz\n"
            "always #5 b_clk = ~b_clk;  // 100MHz"
        )
        ctx["main_clk"] = "a_clk"

        decls = tb_verilog.tdp_signal_decls(ctx, p)
        ctx["dut_signal_decls"] = "\n".join(decls)

        ctx["dut_instance"] = tb_verilog.tdp_dut_instance(ctx, p, top_name)

        inits = tb_verilog.tdp_signal_inits(ctx, p)
        ctx["init_signals"] = "\n    ".join(inits)

        ctx["init_phase"] = tb_verilog.tdp_init_phase(p) if ctx["has_init"] else ""
        ctx["write_phase"] = tb_verilog.tdp_write_phase(ctx, p)
        ctx["read_check_phase"] = tb_verilog.tdp_read_check_phase(ctx, p)

        if has_mask:
            ctx["mask_write_phase"] = tb_verilog.tdp_mask_write_phase(ctx, p)
            ctx["mask_read_check_phase"] = tb_verilog.tdp_mask_read_check_phase(ctx, p)
        else:
            ctx["mask_write_phase"] = ""
            ctx["mask_read_check_phase"] = ""

        return ctx


# ---------------------------------------------------------------------------
# RomTbGen — rom
# ---------------------------------------------------------------------------

class RomTbGen(TbGenerator):

    def build_context(self, mem_spec: MemorySpec, ecc_params: EccParams,
                      interface_type: InterfaceType,
                      top_name: str, phy_wrapper_name: str) -> dict:
        ctx = self._base_context(mem_spec, ecc_params, interface_type, top_name)
        is_l2 = ctx["is_l2"]
        p = "i_" if is_l2 else ""

        ctx["clock_decl"] = "reg clk = 0;"
        ctx["clock_gen"] = "always #5 clk = ~clk;  // 100MHz"
        ctx["main_clk"] = "clk"

        decls = tb_verilog.rom_signal_decls(ctx, p)
        ctx["dut_signal_decls"] = "\n".join(decls)

        ctx["dut_instance"] = tb_verilog.rom_dut_instance(ctx, p, top_name)

        inits = tb_verilog.rom_signal_inits(ctx, p)
        ctx["init_signals"] = "\n    ".join(inits)

        # ROM has no init FSM, no write, no mask
        ctx["init_phase"] = ""
        ctx["write_phase"] = ""  # template handles rom_init via is_rom
        ctx["read_check_phase"] = tb_verilog.rom_read_check_phase(ctx, p)
        ctx["mask_write_phase"] = ""
        ctx["mask_read_check_phase"] = ""

        # ROM memory path for $readmemh (SIM mode: sim_mem in L1)
        if is_l2:
            ctx["rom_mem_path"] = f"dut.u_{phy_wrapper_name}.sim_mem"
        else:
            ctx["rom_mem_path"] = "dut.sim_mem"

        return ctx


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

GENERATORS: dict[str, TbGenerator] = {
    "single_port":    SinglePortTbGen(),
    "dual_port":      DualPortTbGen(),
    "true_dual_port": TrueDualPortTbGen(),
    "rom":            RomTbGen(),
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def gen_tb(
    mem_spec: MemorySpec,
    ecc_params: EccParams,
    interface_type: InterfaceType,
    top_name: str,
    phy_wrapper_name: str,
    tb_outdir: Path,
    sim_outdir: Path,
) -> None:
    """Generate TB, stimulus files, and sim script for one memory instance."""
    tb_outdir.mkdir(parents=True, exist_ok=True)

    # 1. Generate stimulus hex files
    gen_stimulus(mem_spec, interface_type, top_name, tb_outdir,
                 ecc_params=ecc_params)

    # 2. Generate TB Verilog
    generator = GENERATORS[interface_type.base_type]
    tb_text = generator.generate(
        mem_spec, ecc_params, interface_type,
        top_name, phy_wrapper_name,
    )
    tb_path = tb_outdir / f"tb_{top_name}.v"
    tb_path.write_text(tb_text, encoding="utf-8")

    # 3. Generate sim script
    gen_sim_script(top_name, sim_outdir)

