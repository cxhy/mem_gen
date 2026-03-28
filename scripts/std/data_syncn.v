// =============================================================================
// Module      : data_syncn
// Description : Multi-stage pipeline register with synchronous reset
// Note        : Behavioral model for simulation. In real tape-out flow,
//               replace with your standard cell implementation.
// =============================================================================
module data_syncn #(
    parameter DATA_WIDTH  = 1,
    parameter NUM_FLOPS   = 1,
    parameter RESET_VALUE = {DATA_WIDTH{1'b0}}
)(
    input                    clk,
    input                    reset_n,
    input  [DATA_WIDTH-1:0]  data_in,
    output [DATA_WIDTH-1:0]  data_out_sync
);

generate
    if (NUM_FLOPS == 0) begin : g_bypass
        assign data_out_sync = data_in;
    end else begin : g_pipe
        reg [DATA_WIDTH-1:0] stage [0:NUM_FLOPS-1];

        always @(posedge clk or negedge reset_n) begin
            if (!reset_n) begin
                stage[0] <= RESET_VALUE;
            end else begin
                stage[0] <= data_in;
            end
        end

        genvar i;
        for (i = 1; i < NUM_FLOPS; i = i + 1) begin : g_stage
            always @(posedge clk or negedge reset_n) begin
                if (!reset_n) begin
                    stage[i] <= RESET_VALUE;
                end else begin
                    stage[i] <= stage[i-1];
                end
            end
        end

        assign data_out_sync = stage[NUM_FLOPS-1];
    end
endgenerate

endmodule
