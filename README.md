# HF_SDR

Zynq-7000 single-channel HF direct-sampling SDR bring-up project.  The current
design uses the board ADC, a PL DDC chain, AXI DMA scatter-gather receive, and
bare-metal lwIP UDP streaming to make the board behave like a small
RTL-SDR-style receiver over Ethernet.

## Project Status

This project is beyond a raw ADC DMA test.  The current path has been brought
up with:

- LTC2208 ADC capture in PL
- DDS/CIC/FIR based DDC
- AXI4-Stream async FIFO crossing from the 65 MHz ADC/DDC clock to PS FCLK
- AXI DMA S2MM scatter-gather into PS DDR
- Bare-metal UDP IQ streaming
- Runtime center-frequency tuning over UDP/AXI-Lite
- Python receive, FFT, GUI, tune, rtl_tcp, and SpyServer-style bridge tools
- SDR++ connection through the rtl_tcp bridge
- SDR# plugin/bridge experiments under `sdrplugins/`

## Repository Layout

```text
HF_SDR.xpr                         Vivado project
HF_SDR.srcs/sources_1/new/          Custom RTL sources
HF_SDR.srcs/constrs_1/new/          XDC constraints
vitis/sdr_2/src/                    Active bare-metal Vitis application
tools/                              Python host tools
ip_coeffs/                          Filter/IP coefficient files
sdrplugins/                         SDR software integration experiments
```

Generated Vivado/Vitis output is present in the tree but should normally not be
edited by hand:

```text
HF_SDR.gen/
HF_SDR.runs/
HF_SDR.cache/
HF_SDR.hw/
HF_SDR.ip_user_files/
vitis/*/build/
vitis/*/export/
```

## Active Data Path

```text
LTC2208 ch1 ADC on a 16-bit PL input bus
  -> HF_SDR_top
  -> ddc_ip_axis_source
  -> DDS Compiler NCO
  -> signed ADC x cos/sin mixers
  -> CIC Compiler I/Q decimation by 16
  -> FIR Compiler I/Q decimation by 4
  -> IQ packing as int16 stream words
  -> AXIS async FIFO
  -> AXI DMA S2MM scatter-gather
  -> PS DDR
  -> bare-metal lwIP UDP stream
```

`ad_ch2` is present at the top level but is not used by the active DDC path.
DAC outputs are held in a receive-safe quiet/midscale state.

## Key Parameters

- Device: Zynq-7020, observed as `xc7z020-clg400-2`
- ADC/DDC clock: `65 MHz`
- PS AXI/DMA clock: PS FCLK, usually `50 MHz`
- ADC: LTC2208, used through the current 16-bit PL input path
- ADC path width in current RTL: `16 bit`
- ADC input format: offset binary converted to signed two's-complement in RTL
- Default center frequency: `4.9 MHz`
- Total decimation: `64`
- Output IQ sample rate: `65e6 / 64 = 1015625 S/s`
- UDP data port: `5001`
- UDP control/tune port: `5002`

The firmware advertises `1015625` in the UDP header.  Some host-side defaults
may still be stale until the first valid UDP header is received; the packet
header is the authoritative runtime value.

## IP Configuration

Current IP settings from the checked-in `.xci` files:

- `dds_compiler_0`: 65 MHz clock, 32-bit phase, 16-bit sine/cosine output,
  32-bit streaming config/PINC input
- `cic_compiler_0`: fixed decimation by `16`, 5 stages, 24-bit AXIS input,
  40-bit AXIS output
- `fir_compiler_0`: fixed decimation by `4`, 24-bit input, 40-bit output,
  coefficient file `ip_coeffs/fir_decim4_127tap_400k_fs4062k_q15.coe`
- `axis_data_fifo_0`: 16-bit AXIS data, async clocks, TLAST enabled, depth
  `8192`
- `axi_dma_0`: scatter-gather enabled, S2MM enabled, MM2S disabled, 16-bit
  S_AXIS_S2MM stream, 32-bit M_AXI_S2MM

## Packet Format

The PL emits one AXI4-Stream packet every 512 16-bit words:

```text
I0, Q0, I1, Q1, ... I255, Q255
```

Each DMA packet contains:

- `256` IQ samples
- `512` int16 words
- `1024` payload bytes

The UDP stream prepends a 24-byte `SdrUdpHeader` before the IQ payload.

## Runtime Tuning

The DDC tuning AXI-Lite block is mapped at `0x40000000`.

Register map:

```text
0x00  DDS phase increment / PINC, RW
0x04  version, RO, currently 0x48534452 ("HSDR")
0x08  status, RO; write any value to clear sticky status/counters
0x0C  counters, RO
```

Current `status` word from `ddc_ip_axis_source.v`:

```text
bit 0        pack_busy / one-word output pair holding state
bit 1        fir_block_seen / FIR output backpressure sticky flag
bit 2        stall_seen / AXIS output backpressure sticky flag
bit 3        clip_seen
bits 17:4    pack level, currently 0 or 1
bits 31:18   max observed pack level since clear
```

Current `counters` word:

```text
bits 15:0    packet_count
bits 23:16   clip_count low 8 bits
bits 31:24   debug flags:
              bit 0 cic_i_halted_seen
              bit 1 cic_q_halted_seen
              bit 2 cic_input_backpressure_seen
              bit 3 fir_input_backpressure_seen
              bit 4 fir_iq_mismatch_seen
              bit 5 reserved / nofifo in firmware print
              bit 6 axis_stall_seen
              bit 7 pack_block / fifo_full_seen
```

Tune commands are sent to UDP port `5002`:

```text
FREQ <hz>
CENTER <hz>
```

The DDS PINC value is calculated as:

```text
PINC = round(freq_hz * 2^32 / 65e6)
```

For `4.9 MHz`, the expected PINC is `0x134C67FA`.

## Build

The active Vitis application is:

```text
vitis/sdr_2/src/main.c
```

Known working build command from PowerShell:

```powershell
cmd.exe /C "set CC= && set CXX= && D:\Xilinx\2025.2\Vitis\bin\empyro.bat build_app -s d:\vivadoproject\HF_SDR\vitis\sdr_2\src -b d:\vivadoproject\HF_SDR\vitis\sdr_2\build"
```

The generated ELF is expected at:

```text
vitis/sdr_2/build/sdr_2.elf
```

## Network Modes

Network mode is selected at compile time in `vitis/sdr_2/src/main.c`:

```c
#define SDR_USE_DHCP 0
```

Current modes:

- Current checked-in mode is static/direct-connect mode
- DHCP/campus LAN mode targets `PC_IP_ADDR_DHCP = "10.16.25.167"`
- Static/direct-connect mode uses board IP `192.168.1.10`
- Static PC target is `PC_IP_ADDR_STATIC = "192.168.1.100"`

The PS GEM MAC address is currently hard-coded as:

```text
00:0A:35:00:01:02
```

## Host Tools

Useful scripts under `tools/`:

- `udp_rx.py` receives UDP packets and prints rate/loss information
- `fft_viewer.py` shows time-domain and FFT views with Matplotlib
- `sdr_gui.py` provides a smoother PySide6/pyqtgraph live GUI
- `sdr_tune.py` sends UDP center-frequency commands
- `rtl_tcp_bridge.py` exposes the receiver as an rtl_tcp-compatible source
- `hfsdr_spyserver_bridge.py` exposes a SpyServer-style TCP source for SDR#
- `iq_diag.py` checks packet/IQ statistics and can save suspicious payloads
- `capture_fft_bulge.py` captures diagnostic data for intermittent spectrum issues

There is also a C# SDR# plugin experiment under `sdrplugins/HFSDR/`.  Its
receiver updates center frequency and sample rate from the UDP header, although
one default sample-rate constant in that plugin is older than the current
firmware value.

Typical SDR++ integration:

```text
Board UDP stream/control
  -> tools/rtl_tcp_bridge.py on PC
  -> SDR++ Source: RTL-TCP
  -> Host: localhost
  -> Port: 1234
```

## Timing Notes

The ADC/DDC clock and PS FCLK are different-source clocks.  They must not be
treated as synchronously related.  The XDC should keep the asynchronous clock
group between:

```text
clk_out1_design_1_clk_wiz_0_0
clk_fpga_0
```

The real data crossing is through the AXIS async FIFO.  Small control/status
crossings use explicit synchronizers or debug-only status sampling with false
path constraints.

If Vivado reports `TIMING-6`, `TIMING-7`, or `TIMING-51` between these clock
domains, first check that the async clock group constraint is present and
active.

## Debug Checklist

When DMA or UDP streaming looks wrong:

- Check `S2MM_DMASR`
- Confirm the hardware is really AXI DMA scatter-gather mode
- Confirm SG and S2MM masters are mapped to PS HP0 DDR
- Confirm payload length is `1024` bytes
- Confirm TLAST every 512 16-bit stream words
- Confirm startup drain runs after UDP hello packets and before normal stream
- Check DDC status: `pack`, `maxpack`, `fir_block_seen`, `stall_seen`,
  `clip_seen`, and the `dbg` flags
- If `dropped` grows, suspect UDP send/backpressure or PC/network receive
- If `stall_seen=1` or `fir_block_seen=1` after startup drain, real PL-to-DMA
  backpressure occurred
- If `clip_seen=1`, reduce ADC input level or DDC gain/shift

When spectrum looks wrong:

- Confirm input generator frequency versus DDC center
- Confirm `CenterFreqHz` in the UDP header updates after tuning
- Confirm PINC readback at AXI-Lite offset `0x00`
- Check I/Q lane alignment and sign convention for mirror/image problems
- Remember the ADC is direct-sampling at 65 MHz, so frontend anti-alias
  filtering matters for real antennas

## Analog Frontend Notes

The ADC board input has been treated as high impedance during bring-up.  Do not
assume a 50-ohm source is correctly terminated unless an external terminator or
frontend provides that termination.

For serious HF direct sampling, plan for:

- 50-ohm input matching/termination
- ESD/RF input protection
- Optional step attenuator
- HF low-pass filtering below the 65 MHz Nyquist limit
- Optional LNA with bypass
- Careful gain control to avoid overload before the digital `clip_seen` warning

## Known Good Runtime Shape

After startup drain and DDC status clear, a clean run should look roughly like:

```text
pack=0
maxpack=1
fir_block_seen=0
stall_seen=0
clip_seen=0
dropped=0
```
