import argparse
import socket
import struct
import time

import matplotlib.pyplot as plt
import numpy as np


UDP_PORT = 5001
DEFAULT_SAMPLE_RATE = 1_015_650.0
SAMPLES_PER_PACKET = 512
BYTES_PER_SAMPLE = 2
PAYLOAD_BYTES = SAMPLES_PER_PACKET * BYTES_PER_SAMPLE
HEADER_FORMAT = "<4sIHHHH"
HEADER_BYTES = struct.calcsize(HEADER_FORMAT)
MAGIC = b"HFSR"


def parse_args():
    parser = argparse.ArgumentParser(description="HF SDR live FFT viewer")
    parser.add_argument("--port", type=int, default=UDP_PORT,
                        help="UDP listen port")
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE,
                        help="ADC sample rate in samples per second")
    parser.add_argument("--fft-size", type=int, default=16384,
                        help="FFT size")
    parser.add_argument("--max-packets-per-frame", type=int, default=3000,
                        help="maximum UDP packets drained before one plot refresh")
    parser.add_argument("--ref-level", type=float, default=0.0,
                        help="top dBFS display level")
    parser.add_argument("--range-db", type=float, default=120.0,
                        help="vertical display range in dB")
    parser.add_argument("--update-ms", type=int, default=200,
                        help="plot update interval in milliseconds")
    return parser.parse_args()


def parse_packet(data):
    payload = data
    sequence = None

    if len(data) >= HEADER_BYTES:
        magic, seq, header_bytes, sample_count, sample_format, payload_bytes = (
            struct.unpack_from(HEADER_FORMAT, data, 0)
        )

        if magic == MAGIC and header_bytes <= len(data):
            if sample_format != 1 or sample_count == 0:
                return None, None
            payload = data[header_bytes:header_bytes + payload_bytes]
            sequence = seq

    if len(payload) < PAYLOAD_BYTES:
        return None, sequence

    samples = np.frombuffer(payload[:PAYLOAD_BYTES], dtype="<i2").astype(np.float32)
    return samples, sequence


def main():
    args = parse_args()
    if args.fft_size < SAMPLES_PER_PACKET:
        raise ValueError("--fft-size must be at least 512")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", args.port))
    sock.setblocking(False)

    ring_size = args.fft_size * 4
    ring = np.zeros(ring_size, dtype=np.float32)
    write_pos = 0
    buffered = 0
    window = np.hanning(args.fft_size).astype(np.float32)
    freqs = np.fft.rfftfreq(args.fft_size, d=1.0 / args.sample_rate) / 1000.0

    last_seq = None
    total_packets = 0
    lost_packets = 0
    last_rate_time = time.time()
    interval_packets = 0

    plt.ion()
    fig, ax = plt.subplots()
    (line,) = ax.plot(freqs, np.full_like(freqs, -140.0))
    ax.set_title("HF SDR ADC Spectrum")
    ax.set_xlabel("Frequency (kHz)")
    ax.set_ylabel("Magnitude (dBFS)")
    ax.set_xlim(0, args.sample_rate / 2000.0)
    ax.set_ylim(args.ref_level - args.range_db, args.ref_level)
    ax.grid(True, alpha=0.25)
    status = ax.text(0.01, 0.98, "", transform=ax.transAxes,
                     va="top", ha="left")

    print(f"Listening on UDP 0.0.0.0:{args.port}")
    print(f"FFT size={args.fft_size} sample_rate={args.sample_rate:.1f} S/s")

    while plt.fignum_exists(fig.number):
        received = 0

        while received < args.max_packets_per_frame:
            try:
                data, _addr = sock.recvfrom(4096)
            except BlockingIOError:
                break

            samples, seq = parse_packet(data)
            if samples is None:
                continue

            if seq is not None:
                if last_seq is not None:
                    expected = (last_seq + 1) & 0xFFFFFFFF
                    if seq != expected:
                        gap = (seq - expected) & 0xFFFFFFFF
                        if gap < 1000000:
                            lost_packets += gap
                last_seq = seq

            end_pos = write_pos + SAMPLES_PER_PACKET
            if end_pos <= ring_size:
                ring[write_pos:end_pos] = samples
            else:
                first_part = ring_size - write_pos
                ring[write_pos:] = samples[:first_part]
                ring[:end_pos - ring_size] = samples[first_part:]
            write_pos = end_pos % ring_size
            buffered = min(buffered + SAMPLES_PER_PACKET, ring_size)

            total_packets += 1
            interval_packets += 1
            received += 1

        if buffered >= args.fft_size:
            start = (write_pos - args.fft_size) % ring_size
            if start + args.fft_size <= ring_size:
                block = ring[start:start + args.fft_size].copy()
            else:
                first_part = ring_size - start
                block = np.empty(args.fft_size, dtype=np.float32)
                block[:first_part] = ring[start:]
                block[first_part:] = ring[:args.fft_size - first_part]

            block -= np.mean(block)
            spectrum = np.fft.rfft(block * window)
            magnitude = 20.0 * np.log10(np.abs(spectrum) / (args.fft_size * 32768.0) + 1e-12)

            line.set_ydata(magnitude)
            peak_bin = int(np.argmax(magnitude))
            peak_freq = freqs[peak_bin]
            peak_db = magnitude[peak_bin]

            now = time.time()
            elapsed = now - last_rate_time
            if elapsed >= 1.0:
                packet_rate = interval_packets / elapsed
                sample_rate_est = packet_rate * SAMPLES_PER_PACKET
                status.set_text(
                    f"packets={total_packets} lost={lost_packets}\n"
                    f"rate={sample_rate_est:.0f} S/s\n"
                    f"peak={peak_freq:.1f} kHz {peak_db:.1f} dBFS"
                )
                interval_packets = 0
                last_rate_time = now

        if received == 0:
            plt.pause(args.update_ms / 1000.0)
        else:
            plt.pause(0.001)


if __name__ == "__main__":
    main()
