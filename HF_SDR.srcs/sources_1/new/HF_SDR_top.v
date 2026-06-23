`timescale 1ns / 1ps

module HF_SDR_top #(
    parameter integer DECIM_BITS = 6,          // legacy simple ADC decimator setting
    parameter integer AXIS_PACKET_WORDS = 512,
    parameter ADC_OFFSET_BINARY = 1'b1,        // Most high-speed ADCs output offset binary
    parameter [31:0] DDC_PHASE_INC = 32'h13B13B14 // 5 MHz at 65 MHz sample clock
)(
    (* CLOCK_FREQ_HZ = "65000000" *)
    (* ASSOCIATED_RESET = "rst_n" *)
    input  wire        clk,
    input  wire        rst_n,

    // ADC inputs. Only channel 1 is used in this single-channel receiver build.
    input  wire [15:0] ad_ch1,
    input  wire [15:0] ad_ch2,

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
    input  wire        m_axis_tready,

    // AXI-Lite control from PS. Register 0x00 writes DDS phase increment.
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axi, ASSOCIATED_RESET s_axi_aresetn, FREQ_HZ 50000000" *)
    input  wire        s_axi_aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire        s_axi_aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWADDR" *)
    input  wire [3:0]  s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWPROT" *)
    input  wire [2:0]  s_axi_awprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWVALID" *)
    input  wire        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWREADY" *)
    output wire        s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WDATA" *)
    input  wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WSTRB" *)
    input  wire [3:0]  s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WVALID" *)
    input  wire        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WREADY" *)
    output wire        s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BRESP" *)
    output wire [1:0]  s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BVALID" *)
    output wire        s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BREADY" *)
    input  wire        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARADDR" *)
    input  wire [3:0]  s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARPROT" *)
    input  wire [2:0]  s_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARVALID" *)
    input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARREADY" *)
    output wire        s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RDATA" *)
    output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RRESP" *)
    output wire [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RVALID" *)
    output wire        s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RREADY" *)
    input  wire        s_axi_rready
);

    localparam [13:0] DAC_MIDSCALE = 14'h2000;

    wire rst = ~rst_n;
    wire [31:0] dds_phase_inc;
    wire [31:0] ddc_status_word;
    wire [31:0] ddc_counter_word;
    wire ddc_status_clear;

    ddc_tune_axi_lite #(
        .DEFAULT_PHASE_INC(DDC_PHASE_INC)
    ) ddc_tune_axi_lite_i (
        .s_axi_aclk(s_axi_aclk),
        .s_axi_aresetn(s_axi_aresetn),
        .s_axi_awaddr(s_axi_awaddr),
        .s_axi_awprot(s_axi_awprot),
        .s_axi_awvalid(s_axi_awvalid),
        .s_axi_awready(s_axi_awready),
        .s_axi_wdata(s_axi_wdata),
        .s_axi_wstrb(s_axi_wstrb),
        .s_axi_wvalid(s_axi_wvalid),
        .s_axi_wready(s_axi_wready),
        .s_axi_bresp(s_axi_bresp),
        .s_axi_bvalid(s_axi_bvalid),
        .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr),
        .s_axi_arprot(s_axi_arprot),
        .s_axi_arvalid(s_axi_arvalid),
        .s_axi_arready(s_axi_arready),
        .s_axi_rdata(s_axi_rdata),
        .s_axi_rresp(s_axi_rresp),
        .s_axi_rvalid(s_axi_rvalid),
        .s_axi_rready(s_axi_rready),
        .ddc_clk(clk),
        .ddc_rst(rst),
        .ddc_status_word(ddc_status_word),
        .ddc_counter_word(ddc_counter_word),
        .ddc_status_clear(ddc_status_clear),
        .dds_phase_inc(dds_phase_inc)
    );

    ddc_ip_axis_source #(
        .ADC_WIDTH(16),
        .PACKET_WORDS(AXIS_PACKET_WORDS),
        .ADC_OFFSET_BINARY(ADC_OFFSET_BINARY)
    ) ddc_ip_axis_source_i (
        .clk(clk),
        .rst(rst),
        .adc_data(ad_ch1),
        .dds_phase_inc(dds_phase_inc),
        .status_clear(ddc_status_clear),
        .status_word(ddc_status_word),
        .counter_word(ddc_counter_word),
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
