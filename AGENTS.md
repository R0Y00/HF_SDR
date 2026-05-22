# Project Notes

This is a Vivado/Vitis Zynq-7000 SDR bring-up project for a single-channel
shortwave direct-sampling receiver.  The current build is no longer just an
ADC DMA test: it has a working PL DDC chain, AXI DMA scatter-gather receive,
bare-metal UDP streaming, runtime DDC tuning, Python tools, and rtl_tcp style
integration for SDR software.

The practical goal is an RTL-SDR-like receiver using the board ADC and Zynq
PS Ethernet.

## Current Hardware Shape

- Vivado project: `HF_SDR.xpr`
- Device: Zynq-7020, observed as `xc7z020-clg400-2`
- Main top-level is the block-design wrapper:
  - `HF_SDR.gen/sources_1/bd/design_1/hdl/design_1_wrapper.v`
- Custom PL module instantiated in the BD:
  - `HF_SDR.srcs/sources_1/new/HF_SDR_top.v`
- Main custom RTL:
  - `HF_SDR.srcs/sources_1/new/HF_SDR_top.v`
  - `HF_SDR.srcs/sources_1/new/ddc_ip_axis_source.v`
  - `HF_SDR.srcs/sources_1/new/ddc_tune_axi_lite.v`
  - `HF_SDR.srcs/sources_1/new/adc_axis_source.v` is older/simple ADC stream
    code and is not the active DDC path.
  - `HF_SDR.srcs/sources_1/new/ddc_axis_source.v` is older LUT-based DDC
    experiment code and is not the active IP-based DDC path.
- Constraints:
  - `HF_SDR.srcs/constrs_1/new/top.xdc`
- Active Vitis app:
  - `vitis/sdr_2/src/main.c`
- Current generated ELF:
  - `vitis/sdr_2/build/sdr_2.elf`

## Clock Domains

There are two important PL/PS clock domains:

- DDC/ADC processing clock: 65 MHz from `clk_out1_design_1_clk_wiz_0_0`
- PS AXI/DMA clock: PS FCLK, usually 50 MHz, named `clk_fpga_0`

These are different-source clocks.  Do not treat them as synchronously related.
The current XDC intentionally has:

```tcl
set_clock_groups -asynchronous \
    -group [get_clocks clk_out1_design_1_clk_wiz_0_0] \
    -group [get_clocks clk_fpga_0]
```

The real data crossing from PL DDC to PS/DMA is through an AXIS async FIFO.
Small control/status crossings use explicit synchronizers or debug-only status
sampling and are constrained with false paths.

If Vivado reports `TIMING-6`, `TIMING-7`, or `TIMING-51` between
`clk_out1_design_1_clk_wiz_0_0` and `clk_fpga_0`, first check that the above
clock-group constraint is present and active.

## Active PL Data Path

The current receiver datapath is:

```text
AD9238 ch1 12-bit ADC
  -> HF_SDR_top
  -> ddc_ip_axis_source
  -> DDS Compiler NCO
  -> signed ADC x cos/sin mixers
  -> CIC Compiler I path /64
  -> CIC Compiler Q path /64
  -> FIR Compiler I path /4
  -> FIR Compiler Q path /4
  -> internal small IQ FIFO / pack I,Q as int16 stream words
  -> AXIS async FIFO, 65 MHz write side to PS FCLK read side
  -> AXI DMA S2MM scatter-gather
  -> PS DDR
  -> bare-metal lwIP UDP stream
```

`ad_ch2` exists at the top-level but is unused.  DAC outputs are receive-only
safe outputs and are held quiet/midscale by the top logic.

## DDC Parameters

- ADC clock: 65 MHz
- ADC width: 12 bit
- ADC nominal analog range from user: +/-5 V
- ADC input format is treated as offset binary and converted to signed
  two's-complement in RTL.
- DDC default center:
  - currently 4.9 MHz in `vitis/sdr_2/src/main.c`
  - `SDR_CENTER_FREQ_HZ = 4900000`
- DDS phase increment:
  - `PINC = round(freq_hz * 2^32 / 65e6)`
  - for 4.9 MHz: `0x134C67FA`
- Decimation:
  - CIC decimation: /64
  - FIR decimation: /4
  - total: /256
- Output IQ sample rate:
  - `65e6 / 256 = 253906.25 IQ samples/s`

The UDP header currently advertises integer sample rate:

```c
Header->SampleRateHz = (uint32_t)(SDR_ADC_CLK_HZ / 256ULL);
```

So software sees about `253906` S/s.

## DDC IP Configuration Summary

The current DDC uses Xilinx IP blocks plus RTL glue:

- `dds_compiler_0`
  - DDS Compiler
  - configured with programmable/streaming PINC input
  - `s_axis_config_tdata[31:0]` is the PINC word
  - `m_axis_data_tdata[15:0]` is cosine
  - `m_axis_data_tdata[31:16]` is sine
- `cic_compiler_0`
  - CIC Compiler
  - decimation
  - rate 64
  - used twice, one instance for I and one for Q
- `fir_compiler_0`
  - FIR Compiler
  - decimation
  - rate 4
  - used twice, one instance for I and one for Q
  - current coefficient file was a 127-tap decimating low-pass around 105 kHz
    passband in the previous setup
- `axis_data_fifo_0`
  - AXI4-Stream Data FIFO
  - async crossing from 65 MHz DDC domain to PS FCLK DMA domain
  - data width is 16-bit stream words
  - packet/TLAST support must be enabled so DMA sees exact frame boundaries
- `axi_dma_0`
  - AXI DMA in scatter-gather mode
  - S2MM only
  - MM2S disabled
  - S_AXIS_S2MM stream width 16 bits
  - memory map data width 32 bits
  - SG and S2MM masters both mapped to PS HP0 DDR

## AXI DMA / BD Wiring

Current known-good BD intent:

- `axi_dma_0/S_AXIS_S2MM` receives from `axis_data_fifo_0/M_AXIS`
- `axi_dma_0/M_AXI_S2MM` goes through interconnect to
  `processing_system7_0/S_AXI_HP0`
- `axi_dma_0/M_AXI_SG` goes through interconnect to
  `processing_system7_0/S_AXI_HP0`
- `axi_dma_0/S_AXI_LITE` is controlled by PS GP master
- custom `HF_SDR_top_0/s_axi` is controlled by PS GP master
- DMA register base:
  - `0x40400000`
- DDC AXI-Lite register base:
  - `0x40000000`
- Address map observed in Vivado:
  - `/axi_dma_0/Data_S2MM` to HP0 DDR LOWOCM, base `0x0`, range `1G`
  - `/axi_dma_0/Data_SG` to HP0 DDR LOWOCM, base `0x0`, range `1G`
  - `/axi_dma_0/S_AXI_LITE` at `0x40400000`, range `64K`
  - `/HF_SDR_top_0/s_axi` at `0x40000000`, range `4K`

The DMA must be configured as SG.  If the app prints:

```text
DMA is configured as simple mode, expected scatter-gather
```

then the bitstream/XSA or BSP does not match the intended SG hardware.

## AXI-Lite DDC Register Map

Implemented by `ddc_tune_axi_lite.v`:

```text
0x00  DDS phase increment/PINC, RW
0x04  version, RO, currently 0x48534452 ("HSDR")
0x08  status, RO; write any value to clear sticky status/counters
0x0C  counters, RO
```

Current status word format from `ddc_ip_axis_source.v`:

```text
bit 0        iq_fifo_full
bit 1        fifo_full_seen, sticky
bit 2        axis_stall_seen, sticky
bit 3        clip_seen, sticky
bits 16:4    current internal IQ FIFO level
bits 29:17   max internal IQ FIFO level since last clear
bits 31:30   reserved
```

Current counter word:

```text
bits 15:0    packet_count
bits 31:16   clip_count
```

`main.c` prints status like:

```text
[ddc] DDC status=0x00020000 fifo=0 maxfifo=1 full=0 full_seen=0 stall_seen=0 clip_seen=0 packets=14304 clips=0
```

Known-good running status after startup drain:

- `fifo=0`
- `maxfifo=1`
- `full=0`
- `full_seen=0`
- `stall_seen=0`
- `clip_seen=0`
- `dropped=0`

## Packet Format

The PL emits one AXIS packet every 512 16-bit stream words:

```text
I0, Q0, I1, Q1, ... I255, Q255
```

So each DMA packet contains:

- 256 IQ samples
- 512 int16 words
- 1024 payload bytes

The UDP frame prepends a 24-byte header:

```c
typedef struct __attribute__((packed)) {
    uint32_t Magic;        // 0x52534648
    uint32_t Sequence;
    uint16_t HeaderBytes;  // 24
    uint16_t SampleCount;  // 256 IQ samples
    uint16_t SampleFormat; // 2 = IQ int16
    uint16_t PayloadBytes; // 1024
    uint32_t CenterFreqHz;
    uint32_t SampleRateHz;
} SdrUdpHeader;
```

Data UDP port:

- `5001`

Control/tune UDP port:

- `5002`

Tune commands accepted by the firmware:

```text
FREQ <hz>
CENTER <hz>
```

## Vitis App: `vitis/sdr_2`

Active bare-metal application:

- `vitis/sdr_2/src/main.c`

Relevant Xilinx/Vitis embedded software driver/library source paths:

- Main Vitis 2025.2 driver and example library:
  - `D:\Xilinx\2025.2\Vitis\data\embeddedsw\XilinxProcessorIPLib\drivers`
- User also referenced the Vivado-side embedded software tree:
  - `D:\Xilinx\2025.2\data\embeddedsw\XilinxProcessorIPLib\drivers`
- lwIP service library used by the BSP:
  - `D:\Xilinx\2025.2\Vitis\data\embeddedsw\ThirdParty\sw_services\lwip220_v1_3`
- Standalone BSP library used by the current SDT-style app is generated under
  the Vitis platform export tree, currently observed in build output as:
  - `D:\vivadoproject\HF_SDR\vitis\SDR_V2\export\SDR_V2\sw\standalone_ps7_cortexa9_0`

Useful driver areas to inspect when changing PS code:

- AXI DMA:
  - `...\drivers\axidma`
- PS GEM Ethernet:
  - `...\drivers\emacps`
- interrupt controller, if switching SG polling to interrupts later:
  - `...\drivers\scugic`
- cache/MMU/platform helpers are mostly from standalone BSP and generated
  platform include/lib directories.

Important constants:

```c
#define PC_IP_ADDR              "10.16.49.18"
#define PC_UDP_PORT             5001U
#define SDR_CTRL_UDP_PORT       5002U
#define SDR_PACKET_BYTES        1024U
#define SDR_UDP_HEADER_BYTES    24U
#define SDR_SG_RX_BD_COUNT      128U
#define SDR_STARTUP_DRAIN_PACKETS 512U
```

Network mode:

- lwIP 2.2.0
- DHCP enabled
- PS GEM/EMAC base: `0xE000B000`
- MAC address currently hard-coded:
  - `00:0A:35:00:01:02`
- Board can work on campus LAN via DHCP.
- Direct-connect static mode was tested earlier, but current app is DHCP LAN
  mode.

DMA mode:

- AXI DMA SG mode
- S2MM only
- 128 RX BDs
- each BD is 1024 bytes
- BD ring and buffers are cache-aligned
- cache is flushed before handing buffers to DMA
- cache is invalidated after DMA completes a BD
- interrupts are disabled; app polls the RX ring

Startup sequence:

1. Configure DDC frequency/PINC.
2. Start DMA SG ring.
3. Bring up lwIP and DHCP.
4. Create UDP data and control PCBs.
5. Send 20 UDP hello packets.
6. Drain startup DMA backlog by dropping up to 512 completed BDs and requeueing
   them.
7. Clear DDC status.
8. Start normal streaming.

This startup drain is important.  Without it, DHCP and hello delays allow the PL
FIFO to fill before formal streaming starts, producing false `full_seen`,
`stall_seen`, and large `maxfifo` readings.

Known-good latest log shape:

```text
Startup drain: packets=512 bad_len=1 idle_loops=0
[startup-drain] DDC status=0x00020000 fifo=0 maxfifo=1 full=0 full_seen=0 stall_seen=0 clip_seen=0 packets=0 clips=0
[stream-start] DDC status=0x00000000 fifo=0 maxfifo=0 full=0 full_seen=0 stall_seen=0 clip_seen=0 packets=0 clips=0
sent=14336 dropped=0 ...
[ddc] DDC status=0x00020000 fifo=0 maxfifo=1 full=0 full_seen=0 stall_seen=0 clip_seen=0 packets=14304 clips=0
```

The old first SG packet length of 1016 bytes was a startup TLAST alignment issue.
The current startup drain absorbs it.  During normal streaming, bad lengths
should not continue.

Build command that has worked:

```powershell
cmd.exe /C "set CC= && set CXX= && D:\Xilinx\2025.2\Vitis\bin\empyro.bat build_app -s d:\vivadoproject\HF_SDR\vitis\sdr_2\src -b d:\vivadoproject\HF_SDR\vitis\sdr_2\build"
```

## Python Tools

Useful tools under `tools/`:

- `tools/udp_rx.py`
  - receive and print UDP packets/rate/loss
- `tools/fft_viewer.py`
  - Matplotlib time-domain and spectrum viewer
- `tools/sdr_gui.py`
  - PySide6/pyqtgraph GUI, smoother than Matplotlib for live use
- `tools/sdr_tune.py`
  - sends UDP tune commands to port 5002
- `tools/rtl_tcp_bridge.py`
  - exposes this receiver as an rtl_tcp-compatible TCP source, intended for
    SDR++ or similar software
- `tools/capture_bulge.py`
  - diagnostic capture script for intermittent spectrum skirt/bulge events

Previously verified:

- direct UDP receive works
- FFT viewer shows correct tones
- DDC tuning through UDP/AXI-Lite works
- SDR++ can connect through the rtl_tcp bridge
- Center-frequency tuning works from the SDR app path

## SDR++ / rtl_tcp Notes

The working direction for existing SDR applications is:

```text
Board UDP stream/control
  -> tools/rtl_tcp_bridge.py on PC
  -> SDR++ Source: RTL-TCP
  -> Host: localhost
  -> Port: 1234
```

SDR++ has been used successfully.  A center spike can appear in the display; do
not automatically assume it is a transport error.  Check DDC status, input
signal, and frontend/DC behavior.

## Validated Measurements / Bring-Up History

Earlier raw ADC simple-stream mode:

- ADC decimated to about 1.015625 MS/s.
- UDP payload 1024 bytes worked.
- 350 kHz sine input showed correct FFT peak.
- 4.9 MHz function generator input was tested through software DDC.

Current DDC/SG mode:

- Output sample rate is about 253.906 kIQ/s.
- Test input around 4.9 to 5.0 MHz has been verified.
- Runtime tune via AXI-Lite DDS PINC works.
- AXI DMA SG mode is stable.
- Latest clean run has:
  - `dropped=0`
  - `fifo=0`
  - `maxfifo=1`
  - `full_seen=0`
  - `stall_seen=0`
  - `clip_seen=0`

## Analog Frontend Notes

The ADC input is high impedance (`AD9238` board input noted by user), so do not
assume a 50-ohm source is properly terminated unless an external terminator or
frontend provides it.

The intended shortwave frontend direction:

- 50-ohm input matching/termination
- ESD/RF input protection
- optional step attenuator
- HF low-pass before ADC, roughly below Nyquist for 65 MHz sample clock
- optional LNA with bypass
- avoid overloading ADC; `clip_seen` is the digital-side warning but analog
  overdrive/front-end compression can happen before digital clipping

An active wideband loop antenna/LNA module can be used for convenience, but
watch for:

- too much gain into a high-impedance ADC input
- strong broadcast/interference overload
- missing 50-ohm termination
- need for low-pass/anti-alias filtering

For serious HF direct sampling, the analog frontend will matter as much as the
PL/PS data path.

## Timing / Constraints Notes

Current important XDC items:

- I/O pin and LVCMOS33 constraints for ADC/DAC/control pins
- asynchronous clock group between 65 MHz DDC clock and PS FCLK
- false paths for AXI-Lite PINC/control CDC
- false paths for debug/status sampling into the AXI-Lite clock domain

The status false path must include:

```text
packet_count
clip_count
fifo_full_seen
axis_stall_seen
clip_seen
iq_fifo_count
iq_fifo_max_seen
```

If new status bits are added in the DDC clock domain and sampled by
`ddc_tune_axi_lite`, update the false-path regex in `top.xdc`.

The external ADC input timing is still bring-up quality.  Before signoff-quality
ADC capture, add proper input delay constraints relative to the actual ADC data
clock relationship.

## Generated / Build Directories

Do not manually edit generated Vivado/Vitis output unless specifically
inspecting generated artifacts:

- `HF_SDR.gen/`
- `HF_SDR.runs/`
- `HF_SDR.cache/`
- `HF_SDR.hw/`
- `HF_SDR.ip_user_files/`
- `vitis/*/build/`
- `vitis/*/export/`

Prefer source edits in:

- `HF_SDR.srcs/sources_1/new/`
- `HF_SDR.srcs/constrs_1/new/`
- `vitis/sdr_2/src/`
- `tools/`

## Practical Debug Checklist

When DMA/UDP looks wrong:

- Check `S2MM_DMASR`.
- Check whether hardware is really SG mode.
- Check that SG and S2MM masters are mapped to HP0 DDR.
- Check packet length is 1024 payload bytes.
- Check TLAST every 512 16-bit stream words.
- Check startup drain is running after UDP hello and before stream-start.
- Check DDC status:
  - `fifo`
  - `maxfifo`
  - `full_seen`
  - `stall_seen`
  - `clip_seen`
- If `dropped` grows on PS side, suspect UDP send/backpressure or PC/network
  receive.
- If `maxfifo` grows but `dropped` does not, PS is temporarily slower but still
  catching up.
- If `full_seen=1` after startup drain, real PL-to-DMA backpressure occurred.
- If `clip_seen=1`, reduce ADC input level or DDC gain/shift.

When spectrum looks wrong:

- Confirm function generator frequency versus DDC center.
- Confirm `CenterFreqHz` in UDP header updates after tuning.
- Confirm `PINC` readback at AXI-Lite `0x00`.
- Use `capture_bulge.py` or FFT viewer to distinguish real data issues from
  plotting/FFT window artifacts.
- For mirror/image problems, verify I/Q lane alignment and sign convention.
- For aliasing, remember the ADC is direct-sampling at 65 MHz; frontend
  anti-alias filtering is mandatory for real antennas.

## Repository State

This directory is not currently a git repository.
