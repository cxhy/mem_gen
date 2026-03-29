"""Factory functions for constructing test data structures."""

from __future__ import annotations

import math
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config_io import (
    EccConfig, EccModuleInfo, EccParams, MemorySpec, PhysicalConfig,
)

# -- Sub-type name mapping per interface_type --
_SUB_TYPE_MAP = {
    "1rw": "1prf", "1rwm": "1prf",
    "1r1w": "uhd2prf", "1r1wm": "uhd2prf",
    "1r1wa": "2prf", "1r1wma": "2prf",
    "2rw": "dpsram", "2rwm": "dpsram",
    "rom": "rom",
}

# -- Lib mask width: only mask types need it --
_LIB_MASK_WIDTH = {
    "1rwm": 32, "1r1wm": 32, "1r1wma": 32, "2rwm": 32,
}


def make_mem_spec(
    name: str = "test_mem",
    type_: str = "1rw",
    width: int = 32,
    depth: int = 256,
    lib_width: int = 32,
    lib_depth: int = 256,
    input_pipe_stages: int = 0,
    ecc_pipe_stages: int = 0,
    output_pipe_stages: int = 0,
    ecc_enable: bool = False,
    ecc_detailed_report: bool = False,
    ecc_k: int = 64,
    ecc_m: int = 8,
    lib_mask_width: int | None = None,
) -> MemorySpec:
    sub_type = _SUB_TYPE_MAP[type_]
    if lib_mask_width is None:
        lib_mask_width = _LIB_MASK_WIDTH.get(type_, 0)
    ecc = EccConfig(
        enable=ecc_enable,
        code_type="hsiao" if ecc_enable else "",
        data_bits_per_slice=ecc_k if ecc_enable else 0,
        ecc_bits_per_slice=ecc_m if ecc_enable else 0,
        detailed_report=ecc_detailed_report,
    )
    return MemorySpec(
        name=name,
        type=type_,
        width=width,
        depth=depth,
        ecc=ecc,
        physical=PhysicalConfig(
            sub_type=sub_type,
            lib_name=f"TESTLIB_{type_.upper()}_{lib_width}X{lib_depth}",
            lib_width=lib_width,
            lib_depth=lib_depth,
            lib_mask_width=lib_mask_width,
        ),
        ram_rd_latency=1,
        input_pipe_stages=input_pipe_stages,
        ecc_pipe_stages=ecc_pipe_stages,
        output_pipe_stages=output_pipe_stages,
    )


def make_ecc_params(
    width: int,
    k: int = 64,
    m: int = 8,
    enabled: bool = False,
) -> EccParams:
    if not enabled:
        return EccParams(enabled=False, logical_total_width=width)
    n = k + m
    slice_count = math.ceil(width / k)
    data_pad_width = slice_count * k
    ecc_total_bits = slice_count * m
    data_with_ecc_width = slice_count * n
    pad_bits = data_pad_width - width
    return EccParams(
        enabled=True,
        logical_total_width=width,
        slice_count=slice_count,
        data_pad_width=data_pad_width,
        ecc_total_bits=ecc_total_bits,
        data_with_ecc_width=data_with_ecc_width,
        pad_bits=pad_bits,
        k=k,
        m=m,
        n=n,
    )


def make_ecc_modules(prefix: str = "test") -> EccModuleInfo:
    return EccModuleInfo(
        enc_module=f"{prefix}_secded_enc",
        dec_module=f"{prefix}_secded_dec",
        seed_used=42,
    )
