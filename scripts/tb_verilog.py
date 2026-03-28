"""Verilog snippet builders for testbench generation.

Each base_type (SinglePort / DualPort / TrueDualPort / ROM) has:
  - signal_decls:  reg/wire declarations
  - dut_instance:  DUT instantiation string
  - signal_inits:  initial value assignments
  - init_phase:    Init FSM sequence
  - write_phase:   Write data loop
  - read_check_phase:   Burst-issue + pipeline-check loop
  - mask_write_phase:   Mask write loop (mask types only)
  - mask_read_check_phase: Mask read back (mask types only)

Read-check pattern (all types):
  All reads are issued back-to-back (cen held high for NUM_*_VECTORS cycles),
  then drained for TOTAL_RD_LATENCY cycles.  Results are checked as they
  emerge from the pipeline — one per cycle starting at cycle TOTAL_RD_LATENCY.
  This validates back-to-back pipeline throughput rather than one-at-a-time
  sequential latency.

TrueDualPort adds a second pass: B writes then A reads (tests B→A path).
"""

from __future__ import annotations


# ===========================================================================
# ECC helpers (shared)
# ===========================================================================

def _ecc_check_snippet(port_prefix: str, indent: int = 8) -> str:
    """Return Verilog ECC-status check lines.

    Args:
        port_prefix: port prefix string, e.g. "" / "a_" / "b_"
        indent:      leading spaces for each generated line (default 8)
    """
    sp = " " * indent
    sp2 = " " * (indent + 4)
    return (
        f"{sp}if (o_{port_prefix}ecc_correctable_valid !== 1'b0) begin\n"
        f"{sp2}$display(\"ERROR: unexpected correctable ECC error at addr %0d\", i);\n"
        f"{sp2}errors = errors + 1;\n"
        f"{sp}end\n"
        f"{sp}if (o_{port_prefix}ecc_uncorrectable_valid !== 1'b0) begin\n"
        f"{sp2}$display(\"ERROR: unexpected uncorrectable ECC error at addr %0d\", i);\n"
        f"{sp2}errors = errors + 1;\n"
        f"{sp}end\n"
    )


def _ecc_signal_decls(ctx: dict, port_prefix: str) -> list[str]:
    p = "i_" if ctx["is_l2"] else ""
    aw = ctx["addr_width"]
    lines: list[str] = []
    if port_prefix in ("", "a_"):
        lines.extend([
            f"reg              {p}ecc_en;",
            f"reg  [1:0]       {p}ecc_err_insert;",
            f"reg  [1:0]       {p}ecc_err_mask;",
        ])
    lines.extend([
        f"wire             o_{port_prefix}ecc_correctable_valid;",
        f"wire [{aw}-1:0]  o_{port_prefix}ecc_correctable_addr;",
        f"wire             o_{port_prefix}ecc_uncorrectable_valid;",
        f"wire [{aw}-1:0]  o_{port_prefix}ecc_uncorrectable_addr;",
    ])
    return lines


def _ecc_dut_ports(ctx: dict, p: str, port_prefix: str) -> list[str]:
    return [
        f"    .{p}ecc_en         ({p}ecc_en)",
        f"    .{p}ecc_err_insert ({p}ecc_err_insert)",
        f"    .{p}ecc_err_mask   ({p}ecc_err_mask)",
    ]


def _ecc_status_dut_ports(ctx: dict, port_prefix: str) -> list[str]:
    return [
        f"    .o_{port_prefix}ecc_correctable_valid   (o_{port_prefix}ecc_correctable_valid)",
        f"    .o_{port_prefix}ecc_correctable_addr    (o_{port_prefix}ecc_correctable_addr)",
        f"    .o_{port_prefix}ecc_uncorrectable_valid (o_{port_prefix}ecc_uncorrectable_valid)",
        f"    .o_{port_prefix}ecc_uncorrectable_addr  (o_{port_prefix}ecc_uncorrectable_addr)",
    ]


def _ecc_signal_init_lines(port_prefix: str) -> list[str]:
    lines: list[str] = []
    if port_prefix in ("", "a_"):
        lines.extend([
            "i_ecc_en = 1'b1;",
            "i_ecc_err_insert = 2'b00;",
            "i_ecc_err_mask = 2'b00;",
        ])
    return lines


# ===========================================================================
# SinglePort
# ===========================================================================

def sp_signal_decls(ctx: dict, p: str) -> list[str]:
    """Signal declarations for SinglePort TB."""
    dw = ctx["data_width"]
    aw = ctx["addr_width"]
    lines = [
        f"reg              {p}cen;",
        f"reg              {p}wen;",
        f"reg  [{aw}-1:0]  {p}addr;",
        f"reg  [{dw}-1:0]  {p}wdata;",
    ]
    if ctx["has_mask"]:
        mw = ctx["mask_width"]
        lines.append(f"reg  [{mw}-1:0]  {p}bwen;")
    rp = "o_" if ctx["is_l2"] else ""
    lines.append(f"wire [{dw}-1:0]  {rp}rdata;")
    if ctx["has_init"]:
        lines.extend([
            f"reg              {p}init_en;",
            f"reg              {p}init_value;",
            f"wire             o_init_done;",
        ])
    if ctx["has_ecc"]:
        lines.extend(_ecc_signal_decls(ctx, ""))
    return lines


def sp_dut_instance(ctx: dict, p: str, top_name: str) -> str:
    """DUT instantiation for SinglePort."""
    lines = [f"{top_name}_top dut ("]
    ports = [f"    .{p}clk    (clk)"]
    if ctx["is_l2"]:
        ports.append(f"    .i_rst_n  (rst_n)")
    if ctx["has_ecc"]:
        ports.extend(_ecc_dut_ports(ctx, p, ""))
    if ctx["has_init"]:
        ports.extend([
            f"    .{p}init_en    ({p}init_en)",
            f"    .{p}init_value ({p}init_value)",
            f"    .o_init_done  (o_init_done)",
        ])
    ports.append(f"    .{p}cen   ({p}cen)")
    ports.append(f"    .{p}wen   ({p}wen)")
    ports.append(f"    .{p}addr  ({p}addr)")
    ports.append(f"    .{p}wdata ({p}wdata)")
    if ctx["has_mask"]:
        ports.append(f"    .{p}bwen  ({p}bwen)")
    rp = "o_" if ctx["is_l2"] else ""
    ports.append(f"    .{rp}rdata ({rp}rdata)")
    if ctx["has_ecc"]:
        ports.extend(_ecc_status_dut_ports(ctx, ""))
    lines.append(",\n".join(ports))
    lines.append(");")
    return "\n".join(lines)


def sp_signal_inits(ctx: dict, p: str) -> list[str]:
    lines = [
        f"{p}cen = 1'b0;",
        f"{p}wen = 1'b0;",
        f"{p}addr = {ctx['addr_width']}'d0;",
        f"{p}wdata = {ctx['data_width']}'d0;",
    ]
    if ctx["has_mask"]:
        lines.append(f"{p}bwen = {ctx['mask_width']}'d0;")
    if ctx["has_init"]:
        lines.extend([f"{p}init_en = 1'b0;", f"{p}init_value = 1'b0;"])
    if ctx["has_ecc"]:
        lines.extend(_ecc_signal_init_lines(""))
    return lines


def sp_init_phase(p: str) -> str:
    return (
        f"{p}init_en = 1'b1;\n"
        f"    {p}init_value = 1'b0;\n"
        f"    @(posedge clk); #1;\n"
        f"    wait (o_init_done == 1'b1);\n"
        f"    @(posedge clk); #1;\n"
        f"    {p}init_en = 1'b0;\n"
        f"    $display(\"INFO: Init done\");"
    )


def sp_write_phase(ctx: dict, p: str) -> str:
    mask_line = ""
    if ctx["has_mask"]:
        mw = ctx["mask_width"]
        mask_line = f"\n        {p}bwen = {{{mw}{{1'b1}}}};"
    return (
        f"for (i = 0; i < NUM_WRITE_VECTORS; i = i + 1) begin\n"
        f"        @(posedge clk); #1;\n"
        f"        {p}cen = 1'b1;\n"
        f"        {p}wen = 1'b1;\n"
        f"        {p}addr = i[ADDR_WIDTH-1:0];\n"
        f"        {p}wdata = wr_data_mem[i];{mask_line}\n"
        f"    end\n"
        f"    @(posedge clk); #1;\n"
        f"    {p}cen = 1'b0;\n"
        f"    {p}wen = 1'b0;"
    )


def sp_read_check_phase(ctx: dict, p: str) -> str:
    """Burst-issue all reads, then pipeline-check results as they emerge."""
    rp = "o_" if ctx["is_l2"] else ""
    ecc_block = _ecc_check_snippet("", indent=12) if ctx["has_ecc"] else ""
    return (
        f"for (i = 0; i < NUM_READ_VECTORS + TOTAL_RD_LATENCY; i = i + 1) begin\n"
        f"        @(posedge clk); #1;\n"
        f"        if (i < NUM_READ_VECTORS) begin\n"
        f"            {p}cen = 1'b1;\n"
        f"            {p}wen = 1'b0;\n"
        f"            {p}addr = i[ADDR_WIDTH-1:0];\n"
        f"        end else begin\n"
        f"            {p}cen = 1'b0;\n"
        f"        end\n"
        f"        if (i >= TOTAL_RD_LATENCY) begin\n"
        f"            check_rdata(rd_expect_mem[i - TOTAL_RD_LATENCY], {rp}rdata,\n"
        f"                        i - TOTAL_RD_LATENCY);\n"
        f"{ecc_block}"
        f"        end\n"
        f"    end"
    )


def sp_mask_write_phase(ctx: dict, p: str) -> str:
    return (
        f"for (i = 0; i < NUM_MASK_VECTORS; i = i + 1) begin\n"
        f"        @(posedge clk); #1;\n"
        f"        {p}cen = 1'b1;\n"
        f"        {p}wen = 1'b1;\n"
        f"        {p}addr = i[ADDR_WIDTH-1:0];\n"
        f"        {p}wdata = {ctx['data_width']}'h{ctx['mask_new_data_hex']};\n"
        f"        {p}bwen = mask_mem[i];\n"
        f"    end\n"
        f"    @(posedge clk); #1;\n"
        f"    {p}cen = 1'b0;\n"
        f"    {p}wen = 1'b0;"
    )


def sp_mask_read_check_phase(ctx: dict, p: str) -> str:
    """Burst-issue mask reads, then pipeline-check."""
    rp = "o_" if ctx["is_l2"] else ""
    return (
        f"for (i = 0; i < NUM_MASK_VECTORS + TOTAL_RD_LATENCY; i = i + 1) begin\n"
        f"        @(posedge clk); #1;\n"
        f"        if (i < NUM_MASK_VECTORS) begin\n"
        f"            {p}cen = 1'b1;\n"
        f"            {p}wen = 1'b0;\n"
        f"            {p}addr = i[ADDR_WIDTH-1:0];\n"
        f"        end else begin\n"
        f"            {p}cen = 1'b0;\n"
        f"        end\n"
        f"        if (i >= TOTAL_RD_LATENCY) begin\n"
        f"            check_rdata(mask_expect_mem[i - TOTAL_RD_LATENCY], {rp}rdata,\n"
        f"                        i - TOTAL_RD_LATENCY);\n"
        f"        end\n"
        f"    end"
    )


# ===========================================================================
# DualPort
# ===========================================================================

def _dp_mask_port(ctx: dict, p: str) -> str:
    """Return mask port base name for DualPort: L1 uses wr_mask, L2 uses wr_bwen."""
    return "wr_mask" if not ctx["is_l2"] else "wr_bwen"


def dp_signal_decls(ctx: dict, p: str, is_async: bool) -> list[str]:
    dw = ctx["data_width"]
    aw = ctx["addr_width"]
    lines = [
        f"reg              {p}wr_en;",
        f"reg  [{aw}-1:0]  {p}wr_addr;",
        f"reg  [{dw}-1:0]  {p}wr_data;",
    ]
    if ctx["has_mask"]:
        mw = ctx["mask_width"]
        mn = _dp_mask_port(ctx, p)
        lines.append(f"reg  [{mw}-1:0]  {p}{mn};")
    lines.extend([
        f"reg              {p}rd_en;",
        f"reg  [{aw}-1:0]  {p}rd_addr;",
    ])
    rp = "o_" if ctx["is_l2"] else ""
    lines.append(f"wire [{dw}-1:0]  {rp}rd_data;")
    if ctx["has_init"]:
        lines.extend([
            f"reg              {p}init_en;",
            f"reg              {p}init_value;",
            f"wire             o_init_done;",
        ])
    if ctx["has_ecc"]:
        lines.extend(_ecc_signal_decls(ctx, ""))
    return lines


def dp_dut_instance(ctx: dict, p: str, top_name: str,
                    is_async: bool) -> str:
    lines = [f"{top_name}_top dut ("]
    ports: list[str] = []
    if is_async:
        ports.append(f"    .{p}wr_clk  (wr_clk)")
        ports.append(f"    .{p}rd_clk  (rd_clk)")
    else:
        ports.append(f"    .{p}clk     (clk)")
    if ctx["is_l2"]:
        ports.append(f"    .i_rst_n   (rst_n)")
    if ctx["has_ecc"]:
        ports.extend(_ecc_dut_ports(ctx, p, ""))
    if ctx["has_init"]:
        ports.extend([
            f"    .{p}init_en    ({p}init_en)",
            f"    .{p}init_value ({p}init_value)",
            f"    .o_init_done  (o_init_done)",
        ])
    ports.extend([
        f"    .{p}wr_en   ({p}wr_en)",
        f"    .{p}wr_addr ({p}wr_addr)",
        f"    .{p}wr_data ({p}wr_data)",
    ])
    if ctx["has_mask"]:
        mn = _dp_mask_port(ctx, p)
        ports.append(f"    .{p}{mn} ({p}{mn})")
    ports.extend([
        f"    .{p}rd_en   ({p}rd_en)",
        f"    .{p}rd_addr ({p}rd_addr)",
    ])
    rp = "o_" if ctx["is_l2"] else ""
    ports.append(f"    .{rp}rd_data ({rp}rd_data)")
    if ctx["has_ecc"]:
        ports.extend(_ecc_status_dut_ports(ctx, ""))
    lines.append(",\n".join(ports))
    lines.append(");")
    return "\n".join(lines)


def dp_signal_inits(ctx: dict, p: str) -> list[str]:
    lines = [
        f"{p}wr_en = 1'b0;",
        f"{p}wr_addr = {ctx['addr_width']}'d0;",
        f"{p}wr_data = {ctx['data_width']}'d0;",
    ]
    if ctx["has_mask"]:
        mn = _dp_mask_port(ctx, "")
        lines.append(f"{p}{mn} = {ctx['mask_width']}'d0;")
    lines.extend([
        f"{p}rd_en = 1'b0;",
        f"{p}rd_addr = {ctx['addr_width']}'d0;",
    ])
    if ctx["has_init"]:
        lines.extend([f"{p}init_en = 1'b0;", f"{p}init_value = 1'b0;"])
    if ctx["has_ecc"]:
        lines.extend(_ecc_signal_init_lines(""))
    return lines


def dp_init_phase(p: str, wr_clk: str) -> str:
    return (
        f"{p}init_en = 1'b1;\n"
        f"    {p}init_value = 1'b0;\n"
        f"    @(posedge {wr_clk}); #1;\n"
        f"    wait (o_init_done == 1'b1);\n"
        f"    @(posedge {wr_clk}); #1;\n"
        f"    {p}init_en = 1'b0;\n"
        f"    $display(\"INFO: Init done\");"
    )


def dp_write_phase(ctx: dict, p: str, wr_clk: str) -> str:
    mask_line = ""
    if ctx["has_mask"]:
        mw = ctx["mask_width"]
        mn = _dp_mask_port(ctx, p)
        mask_line = f"\n        {p}{mn} = {{{mw}{{1'b1}}}};"
    return (
        f"for (i = 0; i < NUM_WRITE_VECTORS; i = i + 1) begin\n"
        f"        @(posedge {wr_clk}); #1;\n"
        f"        {p}wr_en = 1'b1;\n"
        f"        {p}wr_addr = i[ADDR_WIDTH-1:0];\n"
        f"        {p}wr_data = wr_data_mem[i];{mask_line}\n"
        f"    end\n"
        f"    @(posedge {wr_clk}); #1;\n"
        f"    {p}wr_en = 1'b0;"
    )


def dp_read_check_phase(ctx: dict, p: str, rd_clk: str,
                        is_async: bool) -> str:
    """Burst-issue all reads on rd port, pipeline-check results."""
    rp = "o_" if ctx["is_l2"] else ""
    guard = ""
    if is_async:
        guard = f"repeat(10) @(posedge {rd_clk}); #1;\n    "
    ecc_block = _ecc_check_snippet("", indent=12) if ctx["has_ecc"] else ""
    return guard + (
        f"for (i = 0; i < NUM_READ_VECTORS + TOTAL_RD_LATENCY; i = i + 1) begin\n"
        f"        @(posedge {rd_clk}); #1;\n"
        f"        if (i < NUM_READ_VECTORS) begin\n"
        f"            {p}rd_en = 1'b1;\n"
        f"            {p}rd_addr = i[ADDR_WIDTH-1:0];\n"
        f"        end else begin\n"
        f"            {p}rd_en = 1'b0;\n"
        f"        end\n"
        f"        if (i >= TOTAL_RD_LATENCY) begin\n"
        f"            check_rdata(rd_expect_mem[i - TOTAL_RD_LATENCY], {rp}rd_data,\n"
        f"                        i - TOTAL_RD_LATENCY);\n"
        f"{ecc_block}"
        f"        end\n"
        f"    end"
    )


def dp_mask_write_phase(ctx: dict, p: str, wr_clk: str) -> str:
    mn = _dp_mask_port(ctx, p)
    return (
        f"for (i = 0; i < NUM_MASK_VECTORS; i = i + 1) begin\n"
        f"        @(posedge {wr_clk}); #1;\n"
        f"        {p}wr_en = 1'b1;\n"
        f"        {p}wr_addr = i[ADDR_WIDTH-1:0];\n"
        f"        {p}wr_data = {ctx['data_width']}'h{ctx['mask_new_data_hex']};\n"
        f"        {p}{mn} = mask_mem[i];\n"
        f"    end\n"
        f"    @(posedge {wr_clk}); #1;\n"
        f"    {p}wr_en = 1'b0;"
    )


def dp_mask_read_check_phase(ctx: dict, p: str, rd_clk: str,
                             is_async: bool) -> str:
    """Burst-issue mask reads, pipeline-check."""
    rp = "o_" if ctx["is_l2"] else ""
    guard = ""
    if is_async:
        guard = f"repeat(10) @(posedge {rd_clk}); #1;\n    "
    return guard + (
        f"for (i = 0; i < NUM_MASK_VECTORS + TOTAL_RD_LATENCY; i = i + 1) begin\n"
        f"        @(posedge {rd_clk}); #1;\n"
        f"        if (i < NUM_MASK_VECTORS) begin\n"
        f"            {p}rd_en = 1'b1;\n"
        f"            {p}rd_addr = i[ADDR_WIDTH-1:0];\n"
        f"        end else begin\n"
        f"            {p}rd_en = 1'b0;\n"
        f"        end\n"
        f"        if (i >= TOTAL_RD_LATENCY) begin\n"
        f"            check_rdata(mask_expect_mem[i - TOTAL_RD_LATENCY], {rp}rd_data,\n"
        f"                        i - TOTAL_RD_LATENCY);\n"
        f"        end\n"
        f"    end"
    )


# ===========================================================================
# TrueDualPort
# ===========================================================================

def tdp_signal_decls(ctx: dict, p: str) -> list[str]:
    dw = ctx["data_width"]
    aw = ctx["addr_width"]
    lines: list[str] = []
    for ab in ("a", "b"):
        lines.extend([
            f"reg              {p}{ab}_cen;",
            f"reg              {p}{ab}_wen;",
            f"reg  [{aw}-1:0]  {p}{ab}_addr;",
            f"reg  [{dw}-1:0]  {p}{ab}_wdata;",
        ])
        if ctx["has_mask"]:
            mw = ctx["mask_width"]
            lines.append(f"reg  [{mw}-1:0]  {p}{ab}_bwen;")
        rp = "o_" if ctx["is_l2"] else ""
        lines.append(f"wire [{dw}-1:0]  {rp}{ab}_rdata;")
    if ctx["has_init"]:
        lines.extend([
            f"reg              {p}init_en;",
            f"reg              {p}init_value;",
            f"wire             o_init_done;",
        ])
    if ctx["has_ecc"]:
        lines.extend(_ecc_signal_decls(ctx, "a_"))
        lines.extend(_ecc_signal_decls(ctx, "b_"))
    return lines


def tdp_dut_instance(ctx: dict, p: str, top_name: str) -> str:
    lines = [f"{top_name}_top dut ("]
    ports: list[str] = []
    ports.append(f"    .{p}a_clk   (a_clk)")
    ports.append(f"    .{p}b_clk   (b_clk)")
    if ctx["is_l2"]:
        ports.append(f"    .i_rst_n   (rst_n)")
    if ctx["has_ecc"]:
        ports.extend(_ecc_dut_ports(ctx, p, ""))
    if ctx["has_init"]:
        ports.extend([
            f"    .{p}init_en    ({p}init_en)",
            f"    .{p}init_value ({p}init_value)",
            f"    .o_init_done  (o_init_done)",
        ])
    for ab in ("a", "b"):
        ports.append(f"    .{p}{ab}_cen   ({p}{ab}_cen)")
        ports.append(f"    .{p}{ab}_wen   ({p}{ab}_wen)")
        ports.append(f"    .{p}{ab}_addr  ({p}{ab}_addr)")
        ports.append(f"    .{p}{ab}_wdata ({p}{ab}_wdata)")
        if ctx["has_mask"]:
            ports.append(f"    .{p}{ab}_bwen  ({p}{ab}_bwen)")
        rp = "o_" if ctx["is_l2"] else ""
        ports.append(f"    .{rp}{ab}_rdata ({rp}{ab}_rdata)")
    if ctx["has_ecc"]:
        ports.extend(_ecc_status_dut_ports(ctx, "a_"))
        ports.extend(_ecc_status_dut_ports(ctx, "b_"))
    lines.append(",\n".join(ports))
    lines.append(");")
    return "\n".join(lines)


def tdp_signal_inits(ctx: dict, p: str) -> list[str]:
    lines: list[str] = []
    for ab in ("a", "b"):
        lines.extend([
            f"{p}{ab}_cen = 1'b0;",
            f"{p}{ab}_wen = 1'b0;",
            f"{p}{ab}_addr = {ctx['addr_width']}'d0;",
            f"{p}{ab}_wdata = {ctx['data_width']}'d0;",
        ])
        if ctx["has_mask"]:
            lines.append(f"{p}{ab}_bwen = {ctx['mask_width']}'d0;")
    if ctx["has_init"]:
        lines.extend([f"{p}init_en = 1'b0;", f"{p}init_value = 1'b0;"])
    if ctx["has_ecc"]:
        lines.extend(_ecc_signal_init_lines(""))
    return lines


def tdp_init_phase(p: str) -> str:
    return (
        f"{p}init_en = 1'b1;\n"
        f"    {p}init_value = 1'b0;\n"
        f"    @(posedge a_clk); #1;\n"
        f"    wait (o_init_done == 1'b1);\n"
        f"    @(posedge a_clk); #1;\n"
        f"    {p}init_en = 1'b0;\n"
        f"    $display(\"INFO: Init done\");"
    )


def tdp_write_phase(ctx: dict, p: str) -> str:
    """Port A write phase."""
    mask_line = ""
    if ctx["has_mask"]:
        mw = ctx["mask_width"]
        mask_line = f"\n        {p}a_bwen = {{{mw}{{1'b1}}}};"
    return (
        f"// Port A Write\n"
        f"    for (i = 0; i < NUM_WRITE_VECTORS; i = i + 1) begin\n"
        f"        @(posedge a_clk); #1;\n"
        f"        {p}a_cen = 1'b1;\n"
        f"        {p}a_wen = 1'b1;\n"
        f"        {p}a_addr = i[ADDR_WIDTH-1:0];\n"
        f"        {p}a_wdata = wr_data_mem[i];{mask_line}\n"
        f"    end\n"
        f"    @(posedge a_clk); #1;\n"
        f"    {p}a_cen = 1'b0;\n"
        f"    {p}a_wen = 1'b0;"
    )


def tdp_read_check_phase(ctx: dict, p: str) -> str:
    """Port A write → Port B reads (burst+pipeline).  Tests A→B path."""
    rp = "o_" if ctx["is_l2"] else ""
    ecc_block = _ecc_check_snippet("b_", indent=12) if ctx["has_ecc"] else ""
    return (
        f"// Port A Write -> Port B Read & Check (pipeline)\n"
        f"    for (i = 0; i < NUM_READ_VECTORS + TOTAL_RD_LATENCY; i = i + 1) begin\n"
        f"        @(posedge b_clk); #1;\n"
        f"        if (i < NUM_READ_VECTORS) begin\n"
        f"            {p}b_cen = 1'b1;\n"
        f"            {p}b_wen = 1'b0;\n"
        f"            {p}b_addr = i[ADDR_WIDTH-1:0];\n"
        f"        end else begin\n"
        f"            {p}b_cen = 1'b0;\n"
        f"        end\n"
        f"        if (i >= TOTAL_RD_LATENCY) begin\n"
        f"            check_rdata(rd_expect_mem[i - TOTAL_RD_LATENCY], {rp}b_rdata,\n"
        f"                        i - TOTAL_RD_LATENCY);\n"
        f"{ecc_block}"
        f"        end\n"
        f"    end"
    )


def tdp_b_write_phase(ctx: dict, p: str) -> str:
    """Port B write phase: write b_wr_data_mem to addresses 0..N-1.
    Tests B→A path when combined with tdp_a_read_check_phase.
    """
    mask_line = ""
    if ctx["has_mask"]:
        mw = ctx["mask_width"]
        mask_line = f"\n        {p}b_bwen = {{{mw}{{1'b1}}}};"
    return (
        f"// Port B Write\n"
        f"    for (i = 0; i < NUM_WRITE_VECTORS; i = i + 1) begin\n"
        f"        @(posedge b_clk); #1;\n"
        f"        {p}b_cen = 1'b1;\n"
        f"        {p}b_wen = 1'b1;\n"
        f"        {p}b_addr = i[ADDR_WIDTH-1:0];\n"
        f"        {p}b_wdata = b_wr_data_mem[i];{mask_line}\n"
        f"    end\n"
        f"    @(posedge b_clk); #1;\n"
        f"    {p}b_cen = 1'b0;\n"
        f"    {p}b_wen = 1'b0;"
    )


def tdp_a_read_check_phase(ctx: dict, p: str) -> str:
    """Port B write → Port A reads (burst+pipeline).  Tests B→A path."""
    rp = "o_" if ctx["is_l2"] else ""
    ecc_block = _ecc_check_snippet("a_", indent=12) if ctx["has_ecc"] else ""
    return (
        f"// Port B Write -> Port A Read & Check (pipeline)\n"
        f"    for (i = 0; i < NUM_WRITE_VECTORS + TOTAL_RD_LATENCY; i = i + 1) begin\n"
        f"        @(posedge a_clk); #1;\n"
        f"        if (i < NUM_WRITE_VECTORS) begin\n"
        f"            {p}a_cen = 1'b1;\n"
        f"            {p}a_wen = 1'b0;\n"
        f"            {p}a_addr = i[ADDR_WIDTH-1:0];\n"
        f"        end else begin\n"
        f"            {p}a_cen = 1'b0;\n"
        f"        end\n"
        f"        if (i >= TOTAL_RD_LATENCY) begin\n"
        f"            check_rdata(b_rd_expect_mem[i - TOTAL_RD_LATENCY], {rp}a_rdata,\n"
        f"                        i - TOTAL_RD_LATENCY);\n"
        f"{ecc_block}"
        f"        end\n"
        f"    end"
    )


def tdp_mask_write_phase(ctx: dict, p: str) -> str:
    """Port A mask write phase."""
    return (
        f"// Port A Mask Write\n"
        f"    for (i = 0; i < NUM_MASK_VECTORS; i = i + 1) begin\n"
        f"        @(posedge a_clk); #1;\n"
        f"        {p}a_cen = 1'b1;\n"
        f"        {p}a_wen = 1'b1;\n"
        f"        {p}a_addr = i[ADDR_WIDTH-1:0];\n"
        f"        {p}a_wdata = {ctx['data_width']}'h{ctx['mask_new_data_hex']};\n"
        f"        {p}a_bwen = mask_mem[i];\n"
        f"    end\n"
        f"    @(posedge a_clk); #1;\n"
        f"    {p}a_cen = 1'b0;\n"
        f"    {p}a_wen = 1'b0;"
    )


def tdp_mask_read_check_phase(ctx: dict, p: str) -> str:
    """Port B mask read & check (burst+pipeline)."""
    rp = "o_" if ctx["is_l2"] else ""
    return (
        f"// Port B Mask Read & Check (pipeline)\n"
        f"    for (i = 0; i < NUM_MASK_VECTORS + TOTAL_RD_LATENCY; i = i + 1) begin\n"
        f"        @(posedge b_clk); #1;\n"
        f"        if (i < NUM_MASK_VECTORS) begin\n"
        f"            {p}b_cen = 1'b1;\n"
        f"            {p}b_wen = 1'b0;\n"
        f"            {p}b_addr = i[ADDR_WIDTH-1:0];\n"
        f"        end else begin\n"
        f"            {p}b_cen = 1'b0;\n"
        f"        end\n"
        f"        if (i >= TOTAL_RD_LATENCY) begin\n"
        f"            check_rdata(mask_expect_mem[i - TOTAL_RD_LATENCY], {rp}b_rdata,\n"
        f"                        i - TOTAL_RD_LATENCY);\n"
        f"        end\n"
        f"    end"
    )


# ===========================================================================
# ROM
# ===========================================================================

def rom_signal_decls(ctx: dict, p: str) -> list[str]:
    dw = ctx["data_width"]
    aw = ctx["addr_width"]
    lines = [
        f"reg              {p}cen;",
        f"reg  [{aw}-1:0]  {p}addr;",
    ]
    rp = "o_" if ctx["is_l2"] else ""
    lines.append(f"wire [{dw}-1:0]  {rp}rdata;")
    if ctx["has_ecc"]:
        lines.extend(_ecc_signal_decls(ctx, ""))
    return lines


def rom_dut_instance(ctx: dict, p: str, top_name: str) -> str:
    lines = [f"{top_name}_top dut ("]
    ports: list[str] = []
    ports.append(f"    .{p}clk    (clk)")
    if ctx["is_l2"]:
        ports.append(f"    .i_rst_n  (rst_n)")
    if ctx["has_ecc"]:
        ports.extend(_ecc_dut_ports(ctx, p, ""))
    ports.append(f"    .{p}cen   ({p}cen)")
    ports.append(f"    .{p}addr  ({p}addr)")
    rp = "o_" if ctx["is_l2"] else ""
    ports.append(f"    .{rp}rdata ({rp}rdata)")
    if ctx["has_ecc"]:
        ports.extend(_ecc_status_dut_ports(ctx, ""))
    lines.append(",\n".join(ports))
    lines.append(");")
    return "\n".join(lines)


def rom_signal_inits(ctx: dict, p: str) -> list[str]:
    lines = [
        f"{p}cen = 1'b0;",
        f"{p}addr = {ctx['addr_width']}'d0;",
    ]
    if ctx["has_ecc"]:
        lines.extend(_ecc_signal_init_lines(""))
    return lines


def rom_read_check_phase(ctx: dict, p: str) -> str:
    """Burst-issue ROM reads, pipeline-check results."""
    rp = "o_" if ctx["is_l2"] else ""
    ecc_block = _ecc_check_snippet("", indent=12) if ctx["has_ecc"] else ""
    return (
        f"for (i = 0; i < NUM_READ_VECTORS + TOTAL_RD_LATENCY; i = i + 1) begin\n"
        f"        @(posedge clk); #1;\n"
        f"        if (i < NUM_READ_VECTORS) begin\n"
        f"            {p}cen = 1'b1;\n"
        f"            {p}addr = i[ADDR_WIDTH-1:0];\n"
        f"        end else begin\n"
        f"            {p}cen = 1'b0;\n"
        f"        end\n"
        f"        if (i >= TOTAL_RD_LATENCY) begin\n"
        f"            check_rdata(rd_expect_mem[i - TOTAL_RD_LATENCY], {rp}rdata,\n"
        f"                        i - TOTAL_RD_LATENCY);\n"
        f"{ecc_block}"
        f"        end\n"
        f"    end"
    )
