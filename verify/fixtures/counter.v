`timescale 1ns / 1ps

module counter_tb;
    reg clk;
    reg reset;
    wire [3:0] count;
    wire overflow;

    counter dut (
        .clk(clk),
        .reset(reset),
        .count(count),
        .overflow(overflow)
    );

    initial begin
        $dumpfile("counter.vcd");
        $dumpvars(0, counter_tb);
        clk = 0;
        reset = 1;
        #10 reset = 0;
        #200 $finish;
    end

    always #5 clk = ~clk;
endmodule

module counter (
    input clk,
    input reset,
    output reg [3:0] count,
    output overflow
);
    assign overflow = (count == 4'b1111);

    always @(posedge clk or posedge reset) begin
        if (reset)
            count <= 4'b0;
        else
            count <= count + 1;
    end
endmodule

