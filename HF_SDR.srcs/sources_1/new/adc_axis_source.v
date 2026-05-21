`timescale 1ns / 1ps

module adc_axis_source #(
    parameter integer ADC_WIDTH = 12,
    parameter integer DECIM_BITS = 6,
    parameter integer PACKET_SAMPLES = 512,
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

    reg [ADC_WIDTH-1:0] adc_data_d1;
    reg [ADC_WIDTH-1:0] adc_data_d2;
    reg [DECIM_BITS-1:0] decim_cnt;
    reg [15:0] packet_cnt;

    wire [ADC_WIDTH-1:0] adc_twos =
        ADC_OFFSET_BINARY ? {~adc_data_d2[ADC_WIDTH-1], adc_data_d2[ADC_WIDTH-2:0]} :
                            adc_data_d2;

    wire signed [15:0] adc_s16 =
        {{(16-ADC_WIDTH){adc_twos[ADC_WIDTH-1]}}, adc_twos} << (16 - ADC_WIDTH);

    wire sample_due = &decim_cnt;
    wire can_update = ~m_axis_tvalid | m_axis_tready;
    wire packet_last_sample = packet_cnt == (PACKET_SAMPLES - 1);

    assign m_axis_tkeep = 2'b11;

    always @(posedge clk) begin
        if (rst) begin
            adc_data_d1   <= {ADC_WIDTH{1'b0}};
            adc_data_d2   <= {ADC_WIDTH{1'b0}};
            decim_cnt     <= {DECIM_BITS{1'b0}};
            packet_cnt    <= 16'd0;
            m_axis_tdata  <= 16'd0;
            m_axis_tvalid <= 1'b0;
            m_axis_tlast  <= 1'b0;
        end else begin
            adc_data_d1 <= adc_data;
            adc_data_d2 <= adc_data_d1;
            decim_cnt   <= decim_cnt + {{(DECIM_BITS-1){1'b0}}, 1'b1};

            if (sample_due && can_update) begin
                m_axis_tdata  <= adc_s16;
                m_axis_tvalid <= 1'b1;
                m_axis_tlast  <= packet_last_sample;

                if (packet_last_sample) begin
                    packet_cnt <= 16'd0;
                end else begin
                    packet_cnt <= packet_cnt + 16'd1;
                end
            end else if (m_axis_tready) begin
                m_axis_tvalid <= 1'b0;
                m_axis_tlast  <= 1'b0;
            end
        end
    end

endmodule
