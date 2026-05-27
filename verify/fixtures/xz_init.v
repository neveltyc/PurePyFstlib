`timescale 1ns / 1ps

module xz_init_tb;
    reg [3:0] data;
    reg [7:0] bus;

    initial begin
        $dumpfile("xz_init.vcd");
        $dumpvars(0, xz_init_tb);
        data = 4'bxxxx;
        bus = 8'bzzzzzzzz;
        #10 data = 4'b0011;
        #10 bus = 8'b11001100;
        #10 data = 4'b1100; bus = 8'b00110011;
        #10 $finish;
    end
endmodule
