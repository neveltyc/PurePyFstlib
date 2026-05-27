`timescale 1ns / 1ps

module nested_tb;
    reg clk;
    reg [7:0] data_in;
    wire [7:0] data_out;

    top dut (
        .clk(clk),
        .data_in(data_in),
        .data_out(data_out)
    );

    initial begin
        $dumpfile("nested.vcd");
        $dumpvars(0, nested_tb);
        clk = 0;
        data_in = 8'h00;
        #10 data_in = 8'hA5;
        #10 data_in = 8'h5A;
        #10 data_in = 8'hFF;
        #10 data_in = 8'h00;
        #50 $finish;
    end

    always #5 clk = ~clk;
endmodule

module top (
    input clk,
    input [7:0] data_in,
    output [7:0] data_out
);
    wire [3:0] upper;
    wire [3:0] lower;

    sub_a u_sub_a (
        .clk(clk),
        .in_byte(data_in),
        .upper(upper),
        .lower(lower)
    );

    sub_b u_sub_b (
        .clk(clk),
        .upper(upper),
        .lower(lower),
        .out_byte(data_out)
    );
endmodule

module sub_a (
    input clk,
    input [7:0] in_byte,
    output reg [3:0] upper,
    output reg [3:0] lower
);
    always @(posedge clk) begin
        upper <= in_byte[7:4];
        lower <= in_byte[3:0];
    end
endmodule

module sub_b (
    input clk,
    input [3:0] upper,
    input [3:0] lower,
    output reg [7:0] out_byte
);
    always @(posedge clk) begin
        out_byte <= {upper, lower};
    end
endmodule

