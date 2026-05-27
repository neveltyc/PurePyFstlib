`timescale 1ns / 1ps

module string_tb;
    reg [7:0] status;
    reg [3:0] code;

    initial begin
        $dumpfile("string.vcd");
        $dumpvars(0, string_tb);
        status = 8'h00; code = 4'h0;
        #10 status = 8'h41; code = 4'h1;
        #10 status = 8'h42;
        #10 code = 4'hF;
        #10 $finish;
    end
endmodule
