`timescale 1ns / 1ps

module ddc_ip_axis_source #(
    parameter integer ADC_WIDTH = 16,
    parameter integer PACKET_WORDS = 512,
    parameter ADC_OFFSET_BINARY = 1'b1
)(
    input  wire                 clk,
    input  wire                 rst,
    input  wire [ADC_WIDTH-1:0] adc_data,
    input  wire [31:0]          dds_phase_inc,
    input  wire                 status_clear,
    output wire [31:0]          status_word,
    output wire [31:0]          counter_word,

    output reg  [15:0]          m_axis_tdata,
    output wire [1:0]           m_axis_tkeep,
    output reg                  m_axis_tvalid,
    output reg                  m_axis_tlast,
    input  wire                 m_axis_tready
);

    localparam integer MIX_SHIFT = 15;
    // Five-stage CIC gain is R^5.  With R=16, gain is 16^5 = 2^20.
    localparam integer CIC_TO_FIR_SHIFT = 20;
    localparam integer FIR_TO_S16_SHIFT = 15;
    // Keep post-FIR gain at unity for the current output bandwidth.
    localparam integer OUTPUT_GAIN_SHIFT = 0;
    localparam integer OUTPUT_TO_S16_SHIFT = FIR_TO_S16_SHIFT - OUTPUT_GAIN_SHIFT;

    reg [ADC_WIDTH-1:0] adc_data_d1;
    reg [ADC_WIDTH-1:0] adc_data_d2;
    reg [15:0] packet_cnt;
    reg output_q_word;
    reg output_pair_valid;
    reg signed [15:0] output_i_word;
    reg signed [15:0] output_q_word_data;
    reg [15:0] packet_count;
    reg [15:0] clip_count;
    reg [13:0] fifo_level_seen;
    reg [13:0] fifo_max_seen;
    reg fifo_full_seen;
    reg axis_stall_seen;
    reg clip_seen;
    reg cic_i_halted_seen;
    reg cic_q_halted_seen;
    reg cic_input_backpressure_seen;
    reg fir_input_backpressure_seen;
    reg fir_iq_mismatch_seen;
    reg fir_output_backpressure_seen;

    wire aresetn = ~rst;
    wire [31:0] dds_tdata;
    wire dds_tvalid;
    wire signed [15:0] dds_cos = dds_tdata[15:0];
    wire signed [15:0] dds_sin = dds_tdata[31:16];

    wire [ADC_WIDTH-1:0] adc_twos =
        ADC_OFFSET_BINARY ? {~adc_data_d2[ADC_WIDTH-1], adc_data_d2[ADC_WIDTH-2:0]} :
                            adc_data_d2;

    wire signed [15:0] adc_s16 = adc_to_s16(adc_twos);

    wire signed [31:0] mix_i_full = adc_s16 * dds_cos;
    wire signed [31:0] mix_q_full = -(adc_s16 * dds_sin);
    wire signed [47:0] mix_i_shifted = $signed(mix_i_full) >>> MIX_SHIFT;
    wire signed [47:0] mix_q_shifted = $signed(mix_q_full) >>> MIX_SHIFT;
    wire mix_i_clip = (mix_i_shifted > 48'sd8388607) || (mix_i_shifted < -48'sd8388608);
    wire mix_q_clip = (mix_q_shifted > 48'sd8388607) || (mix_q_shifted < -48'sd8388608);
    wire signed [23:0] mix_i_s24 = sat24(mix_i_shifted);
    wire signed [23:0] mix_q_s24 = sat24(mix_q_shifted);

    wire cic_i_s_tready;
    wire cic_q_s_tready;
    wire cic_i_m_tvalid;
    wire cic_q_m_tvalid;
    wire cic_i_m_tready;
    wire cic_q_m_tready;
    wire cic_i_event_halted;
    wire cic_q_event_halted;
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
    wire cic_input_accept = cic_input_valid && cic_i_s_tready && cic_q_s_tready;
    wire signed [23:0] cic_i_s_tdata = mix_i_s24;
    wire signed [23:0] cic_q_s_tdata = mix_q_s24;
    // Keep the independent I/Q filter IP cores locked to the same sample.
    // A sample only moves across either boundary when both lanes can move.
    wire cic_pair_valid = cic_i_m_tvalid && cic_q_m_tvalid;
    wire fir_input_pair_ready = fir_i_s_tready && fir_q_s_tready;
    wire cic_to_fir_accept = cic_pair_valid && fir_input_pair_ready;
    wire fir_pair_valid = fir_i_m_tvalid && fir_q_m_tvalid;
    wire output_can_advance = ~m_axis_tvalid || m_axis_tready;
    wire output_pair_can_load = !output_pair_valid;
    wire fir_pair_accept = fir_pair_valid && output_pair_can_load;
    wire axis_backpressure_event =
        m_axis_tvalid && !m_axis_tready;
    wire cic_input_backpressure_event =
        cic_input_valid && (!cic_i_s_tready || !cic_q_s_tready);
    wire fir_input_backpressure_event =
        cic_i_m_tvalid && cic_q_m_tvalid && (!fir_i_s_tready || !fir_q_s_tready);
    wire fir_iq_mismatch_event = fir_i_m_tvalid ^ fir_q_m_tvalid;
    wire fir_output_backpressure_event = fir_pair_valid && !output_pair_can_load;
    wire packet_last_word = packet_cnt == (PACKET_WORDS - 1);
    wire output_clip_i = (fir_i_tdata >>> OUTPUT_TO_S16_SHIFT) > 40'sd32767 ||
                         (fir_i_tdata >>> OUTPUT_TO_S16_SHIFT) < -40'sd32768;
    wire output_clip_q = (fir_q_tdata >>> OUTPUT_TO_S16_SHIFT) > 40'sd32767 ||
                         (fir_q_tdata >>> OUTPUT_TO_S16_SHIFT) < -40'sd32768;
    wire clip_event = fir_pair_accept && (mix_i_clip || mix_q_clip || output_clip_i || output_clip_q);

    assign m_axis_tkeep = 2'b11;
    assign cic_i_m_tready = cic_to_fir_accept;
    assign cic_q_m_tready = cic_to_fir_accept;
    assign fir_m_tready = output_pair_can_load;
    assign status_word = {
        fifo_max_seen,
        fifo_level_seen,
        clip_seen,
        axis_stall_seen,
        fifo_full_seen,
        output_pair_valid && !output_pair_can_load
    };
    assign counter_word = {
        fifo_full_seen,
        axis_stall_seen,
        1'b0,
        fir_iq_mismatch_seen,
        fir_input_backpressure_seen,
        cic_input_backpressure_seen,
        cic_q_halted_seen,
        cic_i_halted_seen,
        clip_count[7:0],
        packet_count
    };

    dds_compiler_0 dds_inst (
        .aclk(clk),
        .s_axis_config_tvalid(aresetn),
        .s_axis_config_tdata(dds_phase_inc),
        .m_axis_data_tvalid(dds_tvalid),
        .m_axis_data_tdata(dds_tdata)
    );

    cic_compiler_0 cic_i_inst (
        .aclk(clk),
        .aresetn(aresetn),
        .s_axis_data_tdata(cic_i_s_tdata),
        .s_axis_data_tvalid(cic_input_valid),
        .s_axis_data_tready(cic_i_s_tready),
        .m_axis_data_tdata(cic_i_tdata),
        .m_axis_data_tvalid(cic_i_m_tvalid),
        .m_axis_data_tready(cic_i_m_tready),
        .event_halted(cic_i_event_halted)
    );

    cic_compiler_0 cic_q_inst (
        .aclk(clk),
        .aresetn(aresetn),
        .s_axis_data_tdata(cic_q_s_tdata),
        .s_axis_data_tvalid(cic_input_valid),
        .s_axis_data_tready(cic_q_s_tready),
        .m_axis_data_tdata(cic_q_tdata),
        .m_axis_data_tvalid(cic_q_m_tvalid),
        .m_axis_data_tready(cic_q_m_tready),
        .event_halted(cic_q_event_halted)
    );

    fir_compiler_0 fir_i_inst (
        .aresetn(aresetn),
        .aclk(clk),
        .s_axis_data_tvalid(cic_pair_valid),
        .s_axis_data_tready(fir_i_s_tready),
        .s_axis_data_tdata(fir_i_s_tdata),
        .m_axis_data_tvalid(fir_i_m_tvalid),
        .m_axis_data_tready(fir_m_tready),
        .m_axis_data_tdata(fir_i_tdata)
    );

    fir_compiler_0 fir_q_inst (
        .aresetn(aresetn),
        .aclk(clk),
        .s_axis_data_tvalid(cic_pair_valid),
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
            output_q_word <= 1'b0;
            output_pair_valid <= 1'b0;
            output_i_word <= 16'sd0;
            output_q_word_data <= 16'sd0;
            packet_count  <= 16'd0;
            clip_count    <= 16'd0;
            fifo_level_seen <= 14'd0;
            fifo_max_seen <= 14'd0;
            fifo_full_seen <= 1'b0;
            axis_stall_seen <= 1'b0;
            clip_seen     <= 1'b0;
            cic_i_halted_seen <= 1'b0;
            cic_q_halted_seen <= 1'b0;
            cic_input_backpressure_seen <= 1'b0;
            fir_input_backpressure_seen <= 1'b0;
            fir_iq_mismatch_seen <= 1'b0;
            fir_output_backpressure_seen <= 1'b0;
            m_axis_tdata  <= 16'd0;
            m_axis_tvalid <= 1'b0;
            m_axis_tlast  <= 1'b0;
        end else begin
            adc_data_d1 <= adc_data;
            adc_data_d2 <= adc_data_d1;

            if (status_clear) begin
                packet_count <= 16'd0;
                clip_count <= 16'd0;
                fifo_level_seen <= {13'd0, output_pair_valid};
                fifo_max_seen <= {13'd0, output_pair_valid};
                fifo_full_seen <= 1'b0;
                axis_stall_seen <= 1'b0;
                clip_seen <= 1'b0;
                cic_i_halted_seen <= 1'b0;
                cic_q_halted_seen <= 1'b0;
                cic_input_backpressure_seen <= 1'b0;
                fir_input_backpressure_seen <= 1'b0;
                fir_iq_mismatch_seen <= 1'b0;
                fir_output_backpressure_seen <= 1'b0;
            end else begin
                fifo_level_seen <= {13'd0, output_pair_valid};
                if ({13'd0, output_pair_valid} > fifo_max_seen) begin
                    fifo_max_seen <= {13'd0, output_pair_valid};
                end
                if (fir_output_backpressure_event) begin
                    fifo_full_seen <= 1'b1;
                end
                if (axis_backpressure_event) begin
                    axis_stall_seen <= 1'b1;
                end
                if (cic_i_event_halted) begin
                    cic_i_halted_seen <= 1'b1;
                end
                if (cic_q_event_halted) begin
                    cic_q_halted_seen <= 1'b1;
                end
                if (cic_input_backpressure_event) begin
                    cic_input_backpressure_seen <= 1'b1;
                end
                if (fir_input_backpressure_event) begin
                    fir_input_backpressure_seen <= 1'b1;
                end
                if (fir_iq_mismatch_event) begin
                    fir_iq_mismatch_seen <= 1'b1;
                end
                if (fir_output_backpressure_event) begin
                    fir_output_backpressure_seen <= 1'b1;
                end
                if (clip_event) begin
                    clip_seen <= 1'b1;
                    if (clip_count != 16'hffff) begin
                        clip_count <= clip_count + 16'd1;
                    end
                end
            end

            if (fir_pair_accept) begin
                output_i_word <= sat16(fir_i_tdata >>> OUTPUT_TO_S16_SHIFT);
                output_q_word_data <= sat16(fir_q_tdata >>> OUTPUT_TO_S16_SHIFT);
                output_pair_valid <= 1'b1;
                output_q_word <= 1'b0;
            end

            if (output_can_advance) begin
                if (output_pair_valid || fir_pair_accept) begin
                    m_axis_tvalid <= 1'b1;

                    if (output_q_word) begin
                        m_axis_tdata  <= output_q_word_data;
                        output_q_word <= 1'b0;
                        output_pair_valid <= 1'b0;
                    end else begin
                        m_axis_tdata  <= fir_pair_accept ?
                                         sat16(fir_i_tdata >>> OUTPUT_TO_S16_SHIFT) :
                                         output_i_word;
                        output_q_word <= 1'b1;
                        output_pair_valid <= 1'b1;
                    end

                    m_axis_tlast <= packet_last_word;

                    if (packet_last_word) begin
                        packet_cnt <= 16'd0;
                        if (!status_clear && packet_count != 16'hffff) begin
                            packet_count <= packet_count + 16'd1;
                        end
                    end else begin
                        packet_cnt <= packet_cnt + 16'd1;
                    end
                end else begin
                    m_axis_tvalid <= 1'b0;
                    m_axis_tlast  <= 1'b0;
                    output_q_word <= 1'b0;
                    output_pair_valid <= 1'b0;
                end
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

    function signed [15:0] adc_to_s16;
        input [ADC_WIDTH-1:0] value;
        begin
            adc_to_s16 = $signed(value);
            if (ADC_WIDTH < 16) begin
                adc_to_s16 = adc_to_s16 <<< (16 - ADC_WIDTH);
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
