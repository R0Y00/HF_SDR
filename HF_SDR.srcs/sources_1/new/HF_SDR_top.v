`timescale 1ns / 1ps

module HF_SDR_top #(
    parameter integer DECIM_BITS = 8,          // 65 MHz / 256 = 253.90625 kIQ/s
    parameter integer AXIS_PACKET_WORDS = 512,
    parameter ADC_OFFSET_BINARY = 1'b1,        // Most high-speed ADCs output offset binary
    parameter [31:0] DDC_PHASE_INC = 32'h13B13B14 // 5 MHz at 65 MHz sample clock
)(
    (* CLOCK_FREQ_HZ = "65000000" *)
    (* ASSOCIATED_RESET = "rst_n" *)
    input  wire        clk,
    input  wire        rst_n,

    // ADC inputs. Only channel 1 is used in the first receiver build.
    input  wire [11:0] ad_ch1,
    input  wire [11:0] ad_ch2,

    // ADC/DAC board interface.
    output wire        ad_clk_ch1,
    output wire        ad_clk_ch2,
    output wire [13:0] da_ch1,
    output wire [13:0] da_ch2,
    output wire        da_clk_ch1,
    output wire        da_clk_ch2,
    output wire        da_wrt_ch1,
    output wire        da_wrt_ch2,

    // AXI-Stream output for AXI DMA S2MM.
    output wire [15:0] m_axis_tdata,
    output wire [1:0]  m_axis_tkeep,
    output wire        m_axis_tvalid,
    output wire        m_axis_tlast,
    input  wire        m_axis_tready
);

    localparam [13:0] DAC_MIDSCALE = 14'h2000;

    wire rst = ~rst_n;

    ddc_ip_axis_source #(
        .ADC_WIDTH(12),
        .PACKET_WORDS(AXIS_PACKET_WORDS),
        .ADC_OFFSET_BINARY(ADC_OFFSET_BINARY)
    ) ddc_ip_axis_source_i (
        .clk(clk),
        .rst(rst),
        .adc_data(ad_ch1),
        .m_axis_tdata(m_axis_tdata),
        .m_axis_tkeep(m_axis_tkeep),
        .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tlast(m_axis_tlast),
        .m_axis_tready(m_axis_tready)
    );

    // The first hardware test uses receive only. Keep ADC clocks running and
    // hold both DAC outputs at midscale so the analog outputs stay quiet.
    assign ad_clk_ch1  = clk;
    assign ad_clk_ch2  = clk;
    assign da_clk_ch1  = clk;
    assign da_clk_ch2  = clk;
    assign da_wrt_ch1  = clk;
    assign da_wrt_ch2  = clk;
    assign da_ch1      = DAC_MIDSCALE;
    assign da_ch2      = DAC_MIDSCALE;

    wire unused_ad_ch2 = |ad_ch2;

endmodule
