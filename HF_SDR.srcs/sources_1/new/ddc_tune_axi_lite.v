`timescale 1ns / 1ps

module ddc_tune_axi_lite #(
    parameter [31:0] DEFAULT_PHASE_INC = 32'h13B13B14
)(
    input  wire        s_axi_aclk,
    input  wire        s_axi_aresetn,

    input  wire [3:0]  s_axi_awaddr,
    input  wire [2:0]  s_axi_awprot,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,

    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,

    output reg  [1:0]  s_axi_bresp,
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,

    input  wire [3:0]  s_axi_araddr,
    input  wire [2:0]  s_axi_arprot,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,

    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,

    input  wire        ddc_clk,
    input  wire        ddc_rst,
    input  wire [31:0] ddc_status_word,
    input  wire [31:0] ddc_counter_word,
    output wire        ddc_status_clear,
    output reg  [31:0] dds_phase_inc
);

    localparam [3:0] REG_PHASE_INC = 4'h0;
    localparam [3:0] REG_VERSION   = 4'h4;
    localparam [3:0] REG_STATUS    = 4'h8;
    localparam [3:0] REG_COUNTERS  = 4'hc;
    localparam [31:0] VERSION      = 32'h48534452; // "HSDR"

    reg [3:0] awaddr_reg;
    reg aw_pending;
    reg [31:0] wdata_reg;
    reg [3:0] wstrb_reg;
    reg w_pending;
    reg [31:0] phase_inc_axi;
    reg phase_toggle_axi;
    reg clear_toggle_axi;
    (* ASYNC_REG = "TRUE" *) reg [31:0] status_meta;
    (* ASYNC_REG = "TRUE" *) reg [31:0] status_sync;
    (* ASYNC_REG = "TRUE" *) reg [31:0] counters_meta;
    (* ASYNC_REG = "TRUE" *) reg [31:0] counters_sync;

    (* ASYNC_REG = "TRUE" *) reg [31:0] phase_meta;
    (* ASYNC_REG = "TRUE" *) reg [31:0] phase_sync;
    (* ASYNC_REG = "TRUE" *) reg phase_toggle_meta;
    (* ASYNC_REG = "TRUE" *) reg phase_toggle_sync;
    reg phase_toggle_sync_d;
    (* ASYNC_REG = "TRUE" *) reg clear_toggle_meta;
    (* ASYNC_REG = "TRUE" *) reg clear_toggle_sync;
    reg clear_toggle_sync_d;

    wire write_ready = aw_pending && w_pending && !s_axi_bvalid;
    wire write_phase_inc = write_ready && ((awaddr_reg & 4'hc) == REG_PHASE_INC);

    assign s_axi_awready = !aw_pending;
    assign s_axi_wready = !w_pending;
    assign s_axi_arready = !s_axi_rvalid;

    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            awaddr_reg      <= 4'd0;
            aw_pending      <= 1'b0;
            wdata_reg       <= 32'd0;
            wstrb_reg       <= 4'd0;
            w_pending       <= 1'b0;
            phase_inc_axi   <= DEFAULT_PHASE_INC;
            phase_toggle_axi <= 1'b0;
            clear_toggle_axi <= 1'b0;
            status_meta     <= 32'd0;
            status_sync     <= 32'd0;
            counters_meta   <= 32'd0;
            counters_sync   <= 32'd0;
            s_axi_bresp     <= 2'b00;
            s_axi_bvalid    <= 1'b0;
            s_axi_rdata     <= 32'd0;
            s_axi_rresp     <= 2'b00;
            s_axi_rvalid    <= 1'b0;
        end else begin
            if (s_axi_awready && s_axi_awvalid) begin
                awaddr_reg <= s_axi_awaddr;
                aw_pending <= 1'b1;
            end

            if (s_axi_wready && s_axi_wvalid) begin
                wdata_reg <= s_axi_wdata;
                wstrb_reg <= s_axi_wstrb;
                w_pending <= 1'b1;
            end

            if (write_ready) begin
                case (awaddr_reg & 4'hc)
                    REG_PHASE_INC: begin
                        if (wstrb_reg[0]) phase_inc_axi[7:0]   <= wdata_reg[7:0];
                        if (wstrb_reg[1]) phase_inc_axi[15:8]  <= wdata_reg[15:8];
                        if (wstrb_reg[2]) phase_inc_axi[23:16] <= wdata_reg[23:16];
                        if (wstrb_reg[3]) phase_inc_axi[31:24] <= wdata_reg[31:24];
                        phase_toggle_axi <= ~phase_toggle_axi;
                        s_axi_bresp <= 2'b00;
                    end
                    REG_STATUS: begin
                        clear_toggle_axi <= ~clear_toggle_axi;
                        s_axi_bresp <= 2'b00;
                    end
                    default: begin
                        s_axi_bresp <= 2'b10;
                    end
                endcase

                aw_pending <= 1'b0;
                w_pending <= 1'b0;
                s_axi_bvalid <= 1'b1;
            end else if (s_axi_bvalid && s_axi_bready) begin
                s_axi_bvalid <= 1'b0;
            end

            if (s_axi_arready && s_axi_arvalid) begin
                case (s_axi_araddr & 4'hc)
                    REG_PHASE_INC: begin
                        s_axi_rdata <= phase_inc_axi;
                        s_axi_rresp <= 2'b00;
                    end
                    REG_VERSION: begin
                        s_axi_rdata <= VERSION;
                        s_axi_rresp <= 2'b00;
                    end
                    REG_STATUS: begin
                        s_axi_rdata <= status_sync;
                        s_axi_rresp <= 2'b00;
                    end
                    REG_COUNTERS: begin
                        s_axi_rdata <= counters_sync;
                        s_axi_rresp <= 2'b00;
                    end
                    default: begin
                        s_axi_rdata <= 32'd0;
                        s_axi_rresp <= 2'b10;
                    end
                endcase
                s_axi_rvalid <= 1'b1;
            end else if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
            end

            status_meta <= ddc_status_word;
            status_sync <= status_meta;
            counters_meta <= ddc_counter_word;
            counters_sync <= counters_meta;
        end
    end

    always @(posedge ddc_clk) begin
        if (ddc_rst) begin
            phase_meta          <= DEFAULT_PHASE_INC;
            phase_sync          <= DEFAULT_PHASE_INC;
            phase_toggle_meta   <= 1'b0;
            phase_toggle_sync   <= 1'b0;
            phase_toggle_sync_d <= 1'b0;
            clear_toggle_meta   <= 1'b0;
            clear_toggle_sync   <= 1'b0;
            clear_toggle_sync_d <= 1'b0;
            dds_phase_inc       <= DEFAULT_PHASE_INC;
        end else begin
            phase_meta <= phase_inc_axi;
            phase_sync <= phase_meta;
            phase_toggle_meta <= phase_toggle_axi;
            phase_toggle_sync <= phase_toggle_meta;
            phase_toggle_sync_d <= phase_toggle_sync;
            clear_toggle_meta <= clear_toggle_axi;
            clear_toggle_sync <= clear_toggle_meta;
            clear_toggle_sync_d <= clear_toggle_sync;

            if (phase_toggle_sync != phase_toggle_sync_d) begin
                dds_phase_inc <= phase_sync;
            end
        end
    end

    assign ddc_status_clear = clear_toggle_sync != clear_toggle_sync_d;

    wire unused_axi_prot = |s_axi_awprot | |s_axi_arprot;
    wire unused_write_phase_inc = write_phase_inc;

endmodule
