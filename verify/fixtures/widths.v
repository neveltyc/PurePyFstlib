`timescale 1ns / 1ps

module widths_tb;
    reg clk;
    reg [3:0] counter;
    reg [7:0] byte_val;
    reg [15:0] word_val;

    initial begin
        $dumpfile("widths.vcd");
        $dumpvars(0, widths_tb);
        clk = 0; counter = 4'b0000;
        byte_val = 8'h00; word_val = 16'h0000;
        #10 counter = 4'b0101; byte_val = 8'hA5;
        #10 counter = 4'b1010; word_val = 16'hABCD;
        #10 counter = 4'b1111; byte_val = 8'hFF; word_val = 16'hFFFF;
        #10 $finish;
    end
    always #5 clk = ~clk;
endmodule

