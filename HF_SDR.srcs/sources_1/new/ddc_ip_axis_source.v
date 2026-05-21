`timescale 1ns / 1ps

module ddc_ip_axis_source #(
    parameter integer ADC_WIDTH = 12,
    parameter integer PACKET_WORDS = 512,
    parameter ADC_OFFSET_BINARY = 1'b1
)(
    input  wire                 clk,
    input  wire                 rst,
    input  wire [ADC_WIDTH-1:0] adc_data,

    output reg  [15:0]          m_axis_tdata,
    output wire [1:0]           m_axis_tkeep,
    output reg                  m_axis_tvalid,
    output reg                  m_axis_tlast,
    input  wire                 m_axis_tready
);

    localparam integer MIX_SHIFT = 15;
    localparam integer CIC_TO_FIR_SHIFT = 30;
    localparam integer FIR_TO_S16_SHIFT = 15;

    reg [ADC_WIDTH-1:0] adc_data_d1;
    reg [ADC_WIDTH-1:0] adc_data_d2;
    reg [15:0] packet_cnt;
    reg signed [15:0] pending_i;
    reg signed [15:0] pending_q;
    reg pending_valid;
    reg output_q_word;

    wire aresetn = ~rst;
    wire [31:0] dds_tdata;
    wire dds_tvalid;
    wire signed [15:0] dds_cos = dds_tdata[15:0];
    wire signed [15:0] dds_sin = dds_tdata[31:16];

    wire [ADC_WIDTH-1:0] adc_twos =
        ADC_OFFSET_BINARY ? {~adc_data_d2[ADC_WIDTH-1], adc_data_d2[ADC_WIDTH-2:0]} :
                            adc_data_d2;

    wire signed [15:0] adc_s16 =
        {{(16-ADC_WIDTH){adc_twos[ADC_WIDTH-1]}}, adc_twos} << (16 - ADC_WIDTH);

    wire signed [31:0] mix_i_full = adc_s16 * dds_cos;
    wire signed [31:0] mix_q_full = -(adc_s16 * dds_sin);
    wire signed [23:0] mix_i_s24 = sat24(mix_i_full >>> MIX_SHIFT);
    wire signed [23:0] mix_q_s24 = sat24(mix_q_full >>> MIX_SHIFT);

    wire cic_i_s_tready;
    wire cic_q_s_tready;
    wire cic_i_m_tvalid;
    wire cic_q_m_tvalid;
    wire cic_i_m_tready;
    wire cic_q_m_tready;
    wire signed [47:0] cic_i_tdata;
    wire signed [47:0] cic_q_tdata;
    wire signed [23:0] fir_i_s_tdata = sat24(cic_i_tdata >>> CIC_TO_FIR_SHIFT);
    wire signed [23:0] fir_q_s_tdata = sat24(cic_q_tdata >>> CIC_TO_FIR_SHIFT);

    wire fir_i_s_tready;
    wire fir_q_s_tready;
    wire fir_i_m_tvalid;
    wire fir_q_m_tvalid;
    wire fir_m_tready;
    wire signed [39:0] fir_i_tdata;
    wire signed [39:0] fir_q_tdata;

    wire cic_input_valid = dds_tvalid;
    wire fir_output_valid = fir_i_m_tvalid && fir_q_m_tvalid;
    wire can_accept_fir = ~pending_valid && (~m_axis_tvalid || m_axis_tready);
    wire fir_output_capture = fir_output_valid && can_accept_fir;
    wire packet_last_word = packet_cnt == (PACKET_WORDS - 1);

    assign m_axis_tkeep = 2'b11;
    assign cic_i_m_tready = fir_i_s_tready && fir_q_s_tready;
    assign cic_q_m_tready = fir_i_s_tready && fir_q_s_tready;
    assign fir_m_tready = fir_output_valid && can_accept_fir;

    dds_compiler_0 dds_inst (
        .aclk(clk),
        .m_axis_data_tvalid(dds_tvalid),
        .m_axis_data_tdata(dds_tdata)
    );

    cic_compiler_0 cic_i_inst (
        .aclk(clk),
        .aresetn(aresetn),
        .s_axis_data_tdata(mix_i_s24),
        .s_axis_data_tvalid(cic_input_valid),
        .s_axis_data_tready(cic_i_s_tready),
        .m_axis_data_tdata(cic_i_tdata),
        .m_axis_data_tvalid(cic_i_m_tvalid),
        .m_axis_data_tready(cic_i_m_tready),
        .event_halted()
    );

    cic_compiler_0 cic_q_inst (
        .aclk(clk),
        .aresetn(aresetn),
        .s_axis_data_tdata(mix_q_s24),
        .s_axis_data_tvalid(cic_input_valid),
        .s_axis_data_tready(cic_q_s_tready),
        .m_axis_data_tdata(cic_q_tdata),
        .m_axis_data_tvalid(cic_q_m_tvalid),
        .m_axis_data_tready(cic_q_m_tready),
        .event_halted()
    );

    fir_compiler_0 fir_i_inst (
        .aresetn(aresetn),
        .aclk(clk),
        .s_axis_data_tvalid(cic_i_m_tvalid && cic_q_m_tvalid),
        .s_axis_data_tready(fir_i_s_tready),
        .s_axis_data_tdata(fir_i_s_tdata),
        .m_axis_data_tvalid(fir_i_m_tvalid),
        .m_axis_data_tready(fir_m_tready),
        .m_axis_data_tdata(fir_i_tdata)
    );

    fir_compiler_0 fir_q_inst (
        .aresetn(aresetn),
        .aclk(clk),
        .s_axis_data_tvalid(cic_i_m_tvalid && cic_q_m_tvalid),
        .s_axis_data_tready(fir_q_s_tready),
        .s_axis_data_tdata(fir_q_s_tdata),
        .m_axis_data_tvalid(fir_q_m_tvalid),
        .m_axis_data_tready(fir_m_tready),
        .m_axis_data_tdata(fir_q_tdata)
    );

    always @(posedge clk) begin
        if (rst) begin
            adc_data_d1   <= {ADC_WIDTH{1'b0}};
            adc_data_d2   <= {ADC_WIDTH{1'b0}};
            packet_cnt    <= 16'd0;
            pending_i     <= 16'sd0;
            pending_q     <= 16'sd0;
            pending_valid <= 1'b0;
            output_q_word <= 1'b0;
            m_axis_tdata  <= 16'd0;
            m_axis_tvalid <= 1'b0;
            m_axis_tlast  <= 1'b0;
        end else begin
            adc_data_d1 <= adc_data;
            adc_data_d2 <= adc_data_d1;

            if (fir_output_capture) begin
                pending_i     <= sat16(fir_i_tdata >>> FIR_TO_S16_SHIFT);
                pending_q     <= sat16(fir_q_tdata >>> FIR_TO_S16_SHIFT);
                pending_valid <= 1'b1;
            end

            if (m_axis_tvalid && !m_axis_tready) begin
                m_axis_tvalid <= m_axis_tvalid;
            end else if (pending_valid) begin
                m_axis_tvalid <= 1'b1;

                if (output_q_word) begin
                    m_axis_tdata  <= pending_q;
                    pending_valid <= 1'b0;
                    output_q_word <= 1'b0;
                end else begin
                    m_axis_tdata  <= pending_i;
                    output_q_word <= 1'b1;
                end

                m_axis_tlast <= packet_last_word;

                if (packet_last_word) begin
                    packet_cnt <= 16'd0;
                end else begin
                    packet_cnt <= packet_cnt + 16'd1;
                end
            end else begin
                m_axis_tvalid <= 1'b0;
                m_axis_tlast  <= 1'b0;
            end
        end
    end

    function signed [23:0] sat24;
        input signed [47:0] value;
        begin
            if (value > 48'sd8388607) begin
                sat24 = 24'sd8388607;
            end else if (value < -48'sd8388608) begin
                sat24 = -24'sd8388608;
            end else begin
                sat24 = value[23:0];
            end
        end
    endfunction

    function signed [15:0] sat16;
        input signed [39:0] value;
        begin
            if (value > 40'sd32767) begin
                sat16 = 16'sd32767;
            end else if (value < -40'sd32768) begin
                sat16 = -16'sd32768;
            end else begin
                sat16 = value[15:0];
            end
        end
    endfunction

endmodule
