"""ECC slicing calculation and secded_gen module generation."""

from __future__ import annotations

import math

import secded_gen

from config_io import EccConfig, EccModuleInfo, EccParams


class EccCalculator:
    """Calculate ECC parameters and generate ECC encoder/decoder modules."""

    def calc_params(self, width: int, ecc_config: EccConfig) -> EccParams:
        """Pure calculation, no IO."""
        if not ecc_config.enable:
            return EccParams(
                enabled=False,
                logical_total_width=width,
            )

        k = ecc_config.data_bits_per_slice
        m = ecc_config.ecc_bits_per_slice
        n = k + m

        slice_count = math.ceil(width / k)
        data_pad_width = slice_count * k
        ecc_total_bits = slice_count * m
        data_with_ecc_width = slice_count * n
        pad_bits = data_pad_width - width

        return EccParams(
            enabled=True,
            slice_count=slice_count,
            data_pad_width=data_pad_width,
            ecc_total_bits=ecc_total_bits,
            data_with_ecc_width=data_with_ecc_width,
            pad_bits=pad_bits,
            k=k,
            m=m,
            n=n,
            logical_total_width=data_with_ecc_width,
        )

    def generate_modules(self, ecc_config: EccConfig, prefix: str,
                         outdir: str) -> EccModuleInfo:
        """Call secded_gen to generate enc/dec Verilog files."""
        k = ecc_config.data_bits_per_slice
        m = ecc_config.ecc_bits_per_slice
        n = k + m
        code_type = ecc_config.code_type
        seed = ecc_config.seed

        actual_seed = seed if seed is not None else secded_gen._RND_SEED
        codes = secded_gen.gen_code(code_type, k, m, seed=seed)
        suffix = secded_gen.CODE_OPTIONS[code_type]

        secded_gen.write_enc_dec_files(n, k, m, codes, suffix, outdir, code_type, prefix=prefix)

        module_base = f"{prefix}_secded{suffix}_{n}_{k}"
        return EccModuleInfo(
            enc_module=f"{module_base}_enc",
            dec_module=f"{module_base}_dec",
            seed_used=actual_seed,
        )
