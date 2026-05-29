`timescale 1ns / 1ps

// Benchmark stimulus producing many INDIVIDUALLY-NAMED signals (high max_handle)
// toggling densely over many cycles, yielding several VCDATA sections.  Uses a
// generate block to instantiate flat named regs rather than a memory array
// (which iverilog coalesces in the dump).
module bench_tb;
    parameter CYCLES = 8000;

    reg          clk;
    reg  [63:0]  counter64;
    real         analog;

    initial begin
        $dumpfile("bench.vcd");
        $dumpvars(0, bench_tb);
        clk = 0;
        counter64 = 0;
        analog = 0.0;
    end

    always #5 clk = ~clk;

    always @(posedge clk) begin
        counter64 <= counter64 + 1;
        analog    <= analog + 0.25;
    end

    genvar g;
    generate
        for (g = 0; g < 48; g = g + 1) begin : lane
            reg [23:0] val;
            integer phase;
            initial begin
                val = g * 131 + 7;
                phase = (g % 17) + 1;
            end
            always @(posedge clk) begin
                if ((counter64 % phase) == 0)
                    val <= val + (g + 1);
            end
        end
    endgenerate

    generate
        for (g = 0; g < 32; g = g + 1) begin : bitlane
            reg b;
            integer per;
            initial begin
                b = g[0];
                per = (g % 7) + 2;
            end
            always @(posedge clk) begin
                if ((counter64 % per) == 0)
                    b <= ~b;
            end
        end
    endgenerate

    integer c;
    initial begin
        for (c = 0; c < CYCLES; c = c + 1) @(posedge clk);
        $finish;
    end
endmodule
