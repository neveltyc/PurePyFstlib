`timescale 1ns / 1ps

module alias_tb;
    reg clk;

    initial begin
        $dumpfile("alias.vcd");
        $dumpvars(0, alias_tb);
        clk = 0;
        #50 $finish;
    end
    always #5 clk = ~clk;
endmodule
