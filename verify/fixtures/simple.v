module simple_tb;
    reg clk;
    initial begin
        $dumpfile("simple.vcd");
        $dumpvars(0, simple_tb);
        clk = 1;
        #10 clk = 0;
        #10 $finish;
    end
endmodule
