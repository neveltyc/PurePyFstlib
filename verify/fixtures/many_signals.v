`timescale 1ns / 1ps

module many_signals_tb;
    reg clk;
    reg [7:0] sig [0:19];
    integer i;

    initial begin
        $dumpfile("many_signals.vcd");
        $dumpvars(0, many_signals_tb);
        clk = 0;
        for (i = 0; i < 20; i = i + 1) sig[i] = i;
        #10 for (i = 0; i < 20; i = i + 1) sig[i] = sig[i] + 1;
        #10 for (i = 0; i < 20; i = i + 1) sig[i] = sig[i] + 1;
        #10 $finish;
    end
    always #5 clk = ~clk;
endmodule
