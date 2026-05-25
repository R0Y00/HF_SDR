`timescale 1ns / 1ps

module ddc_ip_axis_source #(
    parameter integer ADC_WIDTH = 12,
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
    localparam integer CIC_TO_FIR_SHIFT = 30;
    localparam integer FIR_TO_S16_SHIFT = 15;
    // Post-FIR digital gain. 0 = original scaling, 2 = +12 dB.
    // This helps weak HF signals use more of the 16-bit UDP sample range.
    localparam integer OUTPUT_GAIN_SHIFT = 2;
    localparam integer OUTPUT_TO_S16_SHIFT = FIR_TO_S16_SHIFT - OUTPUT_GAIN_SHIFT;
    localparam integer IQ_FIFO_ADDR_BITS = 12;
    localparam integer IQ_FIFO_DEPTH = (1 << IQ_FIFO_ADDR_BITS);
    localparam [IQ_FIFO_ADDR_BITS:0] IQ_FIFO_COUNT_MAX = IQ_FIFO_DEPTH;
    localparam [IQ_FIFO_ADDR_BITS:0] STALL_REPORT_LEVEL = 64;

    reg [ADC_WIDTH-1:0] adc_data_d1;
    reg [ADC_WIDTH-1:0] adc_data_d2;
    reg [15:0] packet_cnt;
    reg output_q_word;
    reg [IQ_FIFO_ADDR_BITS-1:0] iq_wr_ptr;
    reg [IQ_FIFO_ADDR_BITS-1:0] iq_rd_ptr;
    reg [IQ_FIFO_ADDR_BITS:0] iq_fifo_count;
    reg signed [15:0] iq_fifo_i [0:IQ_FIFO_DEPTH-1];
    reg signed [15:0] iq_fifo_q [0:IQ_FIFO_DEPTH-1];
    reg [15:0] packet_count;
    reg [15:0] clip_count;
    reg [IQ_FIFO_ADDR_BITS:0] iq_fifo_max_seen;
    reg fifo_full_seen;
    reg axis_stall_seen;
    reg clip_seen;

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
    // Keep the independent I/Q filter IP cores locked to the same sample.
    // A sample only moves across either boundary when both lanes can move.
    wire cic_to_fir_accept =
        cic_i_m_tvalid && cic_q_m_tvalid && fir_i_s_tready && fir_q_s_tready;
    wire fir_pair_valid = fir_i_m_tvalid && fir_q_m_tvalid;
    wire iq_fifo_full = iq_fifo_count == IQ_FIFO_COUNT_MAX;
    wire fir_pair_accept = fir_pair_valid && ~iq_fifo_full;
    wire output_can_advance = ~m_axis_tvalid || m_axis_tready;
    wire axis_backpressure_event =
        m_axis_tvalid && !m_axis_tready && (iq_fifo_count >= STALL_REPORT_LEVEL);
    wire packet_last_word = packet_cnt == (PACKET_WORDS - 1);
    wire output_clip_i = (fir_i_tdata >>> OUTPUT_TO_S16_SHIFT) > 40'sd32767 ||
                         (fir_i_tdata >>> OUTPUT_TO_S16_SHIFT) < -40'sd32768;
    wire output_clip_q = (fir_q_tdata >>> OUTPUT_TO_S16_SHIFT) > 40'sd32767 ||
                         (fir_q_tdata >>> OUTPUT_TO_S16_SHIFT) < -40'sd32768;
    wire clip_event = fir_pair_accept && (mix_i_clip || mix_q_clip || output_clip_i || output_clip_q);

    assign m_axis_tkeep = 2'b11;
    assign cic_i_m_tready = cic_to_fir_accept;
    assign cic_q_m_tready = cic_to_fir_accept;
    assign fir_m_tready = fir_pair_accept;
    assign status_word = {
        2'd0,
        iq_fifo_max_seen,
        iq_fifo_count,
        clip_seen,
        axis_stall_seen,
        fifo_full_seen,
        iq_fifo_full
    };
    assign counter_word = {clip_count, packet_count};

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
        .s_axis_data_tvalid(cic_to_fir_accept),
        .s_axis_data_tready(fir_i_s_tready),
        .s_axis_data_tdata(fir_i_s_tdata),
        .m_axis_data_tvalid(fir_i_m_tvalid),
        .m_axis_data_tready(fir_m_tready),
        .m_axis_data_tdata(fir_i_tdata)
    );

    fir_compiler_0 fir_q_inst (
        .aresetn(aresetn),
        .aclk(clk),
        .s_axis_data_tvalid(cic_to_fir_accept),
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
            iq_wr_ptr     <= {IQ_FIFO_ADDR_BITS{1'b0}};
            iq_rd_ptr     <= {IQ_FIFO_ADDR_BITS{1'b0}};
            iq_fifo_count <= {(IQ_FIFO_ADDR_BITS+1){1'b0}};
            packet_count  <= 16'd0;
            clip_count    <= 16'd0;
            iq_fifo_max_seen <= {(IQ_FIFO_ADDR_BITS+1){1'b0}};
            fifo_full_seen <= 1'b0;
            axis_stall_seen <= 1'b0;
            clip_seen     <= 1'b0;
            m_axis_tdata  <= 16'd0;
            m_axis_tvalid <= 1'b0;
            m_axis_tlast  <= 1'b0;
        end else begin
            adc_data_d1 <= adc_data;
            adc_data_d2 <= adc_data_d1;

            if (status_clear) begin
                packet_count <= 16'd0;
                clip_count <= 16'd0;
                iq_fifo_max_seen <= {(IQ_FIFO_ADDR_BITS+1){1'b0}};
                fifo_full_seen <= 1'b0;
                axis_stall_seen <= 1'b0;
                clip_seen <= 1'b0;
            end else begin
                if (iq_fifo_count > iq_fifo_max_seen) begin
                    iq_fifo_max_seen <= iq_fifo_count;
                end
                if (iq_fifo_full) begin
                    fifo_full_seen <= 1'b1;
                end
                if (axis_backpressure_event) begin
                    axis_stall_seen <= 1'b1;
                end
                if (clip_event) begin
                    clip_seen <= 1'b1;
                    if (clip_count != 16'hffff) begin
                        clip_count <= clip_count + 16'd1;
                    end
                end
            end

            if (fir_pair_accept) begin
                iq_fifo_i[iq_wr_ptr] <= sat16(fir_i_tdata >>> OUTPUT_TO_S16_SHIFT);
                iq_fifo_q[iq_wr_ptr] <= sat16(fir_q_tdata >>> OUTPUT_TO_S16_SHIFT);
                iq_wr_ptr <= iq_wr_ptr + 1'b1;
            end

            if (output_can_advance) begin
                if (iq_fifo_count != 0) begin
                    m_axis_tvalid <= 1'b1;

                    if (output_q_word) begin
                        m_axis_tdata  <= iq_fifo_q[iq_rd_ptr];
                        output_q_word <= 1'b0;
                        iq_rd_ptr     <= iq_rd_ptr + 1'b1;
                    end else begin
                        m_axis_tdata  <= iq_fifo_i[iq_rd_ptr];
                        output_q_word <= 1'b1;
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

                    if (fir_pair_accept && output_q_word) begin
                        iq_fifo_count <= iq_fifo_count;
                    end else if (fir_pair_accept) begin
                        iq_fifo_count <= iq_fifo_count + 1'b1;
                    end else if (output_q_word) begin
                        iq_fifo_count <= iq_fifo_count - 1'b1;
                    end
                end else begin
                    m_axis_tvalid <= 1'b0;
                    m_axis_tlast  <= 1'b0;
                    output_q_word <= 1'b0;
                    if (fir_pair_accept) begin
                        iq_fifo_count <= {{IQ_FIFO_ADDR_BITS{1'b0}}, 1'b1};
                    end else begin
                        iq_fifo_count <= {(IQ_FIFO_ADDR_BITS+1){1'b0}};
                    end
                end
            end else begin
                if (fir_pair_accept) begin
                    iq_fifo_count <= iq_fifo_count + 1'b1;
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
