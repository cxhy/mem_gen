// =============================================================================
// Module      : std_dffe
// Description : D Flip-Flop with clock enable (behavioral model)
// Note        : This is a behavioral placeholder. In real tape-out flow,
//               replace this file with your foundry std cell wrapper.
// =============================================================================
module std_dffe #(
    parameter WIDTH = 1
)(
    input              clk,
    input              en,
    input  [WIDTH-1:0] d,
    output [WIDTH-1:0] q
);

reg [WIDTH-1:0] q_r;

always @(posedge clk) begin
    if (en)
        q_r <= d;
end

assign q = q_r;

endmodule
