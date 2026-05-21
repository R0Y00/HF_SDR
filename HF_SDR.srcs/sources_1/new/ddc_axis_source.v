`timescale 1ns / 1ps

module ddc_axis_source #(
    parameter integer ADC_WIDTH = 12,
    parameter integer DECIM_BITS = 8,
    parameter integer PACKET_WORDS = 512,
    parameter ADC_OFFSET_BINARY = 1'b1,
    parameter [31:0] PHASE_INC = 32'h13B13B14
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
    localparam integer ACC_WIDTH = 40;

    reg [ADC_WIDTH-1:0] adc_data_d1;
    reg [ADC_WIDTH-1:0] adc_data_d2;
    reg [DECIM_BITS-1:0] decim_cnt;
    reg [15:0] packet_cnt;
    reg [31:0] phase_acc;
    reg signed [ACC_WIDTH-1:0] acc_i;
    reg signed [ACC_WIDTH-1:0] acc_q;
    reg signed [15:0] pending_i;
    reg signed [15:0] pending_q;
    reg pending_valid;
    reg output_q_word;

    wire [ADC_WIDTH-1:0] adc_twos =
        ADC_OFFSET_BINARY ? {~adc_data_d2[ADC_WIDTH-1], adc_data_d2[ADC_WIDTH-2:0]} :
                            adc_data_d2;

    wire signed [15:0] adc_s16 =
        {{(16-ADC_WIDTH){adc_twos[ADC_WIDTH-1]}}, adc_twos} << (16 - ADC_WIDTH);

    wire [5:0] phase_addr = phase_acc[31:26];
    wire signed [15:0] sin_val;
    wire signed [15:0] cos_val;
    wire signed [31:0] mix_i_full = adc_s16 * cos_val;
    wire signed [31:0] mix_q_full = -(adc_s16 * sin_val);
    wire signed [31:0] mix_i = mix_i_full >>> MIX_SHIFT;
    wire signed [31:0] mix_q = mix_q_full >>> MIX_SHIFT;
    wire signed [ACC_WIDTH-1:0] next_acc_i = acc_i + {{(ACC_WIDTH-32){mix_i[31]}}, mix_i};
    wire signed [ACC_WIDTH-1:0] next_acc_q = acc_q + {{(ACC_WIDTH-32){mix_q[31]}}, mix_q};
    wire decim_last = &decim_cnt;
    wire can_load_output = ~pending_valid && (~m_axis_tvalid || m_axis_tready);
    wire packet_last_word = packet_cnt == (PACKET_WORDS - 1);

    assign m_axis_tkeep = 2'b11;

    sin_lut_64 sin_lut_i (
        .addr(phase_addr),
        .data(sin_val)
    );

    sin_lut_64 cos_lut_i (
        .addr(phase_addr + 6'd16),
        .data(cos_val)
    );

    always @(posedge clk) begin
        if (rst) begin
            adc_data_d1   <= {ADC_WIDTH{1'b0}};
            adc_data_d2   <= {ADC_WIDTH{1'b0}};
            decim_cnt     <= {DECIM_BITS{1'b0}};
            packet_cnt    <= 16'd0;
            phase_acc     <= 32'd0;
            acc_i         <= {ACC_WIDTH{1'b0}};
            acc_q         <= {ACC_WIDTH{1'b0}};
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
            phase_acc   <= phase_acc + PHASE_INC;
            decim_cnt   <= decim_cnt + {{(DECIM_BITS-1){1'b0}}, 1'b1};

            if (decim_last) begin
                acc_i <= {ACC_WIDTH{1'b0}};
                acc_q <= {ACC_WIDTH{1'b0}};

                if (can_load_output) begin
                    pending_i     <= sat16(next_acc_i >>> DECIM_BITS);
                    pending_q     <= sat16(next_acc_q >>> DECIM_BITS);
                    pending_valid <= 1'b1;
                end
            end else begin
                acc_i <= next_acc_i;
                acc_q <= next_acc_q;
            end

            if (m_axis_tvalid && !m_axis_tready) begin
                m_axis_tvalid <= m_axis_tvalid;
            end else if (pending_valid) begin
                m_axis_tvalid <= 1'b1;

                if (output_q_word) begin
                    m_axis_tdata  <= pending_q;
                    m_axis_tlast  <= packet_last_word;
                    pending_valid <= 1'b0;
                    output_q_word <= 1'b0;
                end else begin
                    m_axis_tdata  <= pending_i;
                    m_axis_tlast  <= packet_last_word;
                    output_q_word <= 1'b1;
                end

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

    function signed [15:0] sat16;
        input signed [ACC_WIDTH-1:0] value;
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

module sin_lut_64 (
    input  wire [5:0]  addr,
    output reg  signed [15:0] data
);

    always @(*) begin
        case (addr)
            6'd0:  data = 16'sd0;
            6'd1:  data = 16'sd3212;
            6'd2:  data = 16'sd6393;
            6'd3:  data = 16'sd9512;
            6'd4:  data = 16'sd12539;
            6'd5:  data = 16'sd15446;
            6'd6:  data = 16'sd18204;
            6'd7:  data = 16'sd20787;
            6'd8:  data = 16'sd23170;
            6'd9:  data = 16'sd25329;
            6'd10: data = 16'sd27245;
            6'd11: data = 16'sd28898;
            6'd12: data = 16'sd30273;
            6'd13: data = 16'sd31356;
            6'd14: data = 16'sd32137;
            6'd15: data = 16'sd32609;
            6'd16: data = 16'sd32767;
            6'd17: data = 16'sd32609;
            6'd18: data = 16'sd32137;
            6'd19: data = 16'sd31356;
            6'd20: data = 16'sd30273;
            6'd21: data = 16'sd28898;
            6'd22: data = 16'sd27245;
            6'd23: data = 16'sd25329;
            6'd24: data = 16'sd23170;
            6'd25: data = 16'sd20787;
            6'd26: data = 16'sd18204;
            6'd27: data = 16'sd15446;
            6'd28: data = 16'sd12539;
            6'd29: data = 16'sd9512;
            6'd30: data = 16'sd6393;
            6'd31: data = 16'sd3212;
            6'd32: data = 16'sd0;
            6'd33: data = -16'sd3212;
            6'd34: data = -16'sd6393;
            6'd35: data = -16'sd9512;
            6'd36: data = -16'sd12539;
            6'd37: data = -16'sd15446;
            6'd38: data = -16'sd18204;
            6'd39: data = -16'sd20787;
            6'd40: data = -16'sd23170;
            6'd41: data = -16'sd25329;
            6'd42: data = -16'sd27245;
            6'd43: data = -16'sd28898;
            6'd44: data = -16'sd30273;
            6'd45: data = -16'sd31356;
            6'd46: data = -16'sd32137;
            6'd47: data = -16'sd32609;
            6'd48: data = -16'sd32767;
            6'd49: data = -16'sd32609;
            6'd50: data = -16'sd32137;
            6'd51: data = -16'sd31356;
            6'd52: data = -16'sd30273;
            6'd53: data = -16'sd28898;
            6'd54: data = -16'sd27245;
            6'd55: data = -16'sd25329;
            6'd56: data = -16'sd23170;
            6'd57: data = -16'sd20787;
            6'd58: data = -16'sd18204;
            6'd59: data = -16'sd15446;
            6'd60: data = -16'sd12539;
            6'd61: data = -16'sd9512;
            6'd62: data = -16'sd6393;
            6'd63: data = -16'sd3212;
            default: data = 16'sd0;
        endcase
    end

endmodule
