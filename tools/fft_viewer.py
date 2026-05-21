import argparse
import socket
import struct
import threading
import time

import matplotlib.pyplot as plt
import numpy as np


UDP_PORT = 5001
DEFAULT_SAMPLE_RATE = 65_000_000.0 / 256.0
DEFAULT_CENTER_FREQ = 5_000_000.0
WORDS_PER_PACKET = 512
IQ_SAMPLES_PER_PACKET = 256
BYTES_PER_SAMPLE = 2
PAYLOAD_BYTES = WORDS_PER_PACKET * BYTES_PER_SAMPLE
HEADER_FORMAT = "<4sIHHHH"
HEADER_BYTES = struct.calcsize(HEADER_FORMAT)
MAGIC = b"HFSR"


def parse_args():
    parser = argparse.ArgumentParser(description="HF SDR live FFT viewer")
    parser.add_argument("--port", type=int, default=UDP_PORT,
                        help="UDP listen port")
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE,
                        help="DDC output sample rate in IQ samples per second")
    parser.add_argument("--center-frequency", type=float,
                        default=DEFAULT_CENTER_FREQ,
                        help="DDC RF center frequency in Hz")
    parser.add_argument("--fft-size", type=int, default=16384,
                        help="FFT size")
    parser.add_argument("--scope-size", type=int, default=2048,
                        help="number of samples shown in the time-domain plot")
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
    sample_format = 1
    sample_count = WORDS_PER_PACKET

    if len(data) >= HEADER_BYTES:
        magic, seq, header_bytes, sample_count, sample_format, payload_bytes = (
            struct.unpack_from(HEADER_FORMAT, data, 0)
        )

        if magic == MAGIC and header_bytes <= len(data):
            if sample_format not in (1, 2) or sample_count == 0:
                return None, None
            payload = data[header_bytes:header_bytes + payload_bytes]
            sequence = seq

    if len(payload) < PAYLOAD_BYTES:
        return None, sequence

    words = np.frombuffer(payload[:PAYLOAD_BYTES], dtype="<i2")
    if sample_format == 2:
        words = words[:sample_count * 2].astype(np.float32)
        samples = words[0::2] + 1j * words[1::2]
        samples = samples.astype(np.complex64)
    else:
        words = words[:sample_count].astype(np.float32)
        samples = words.astype(np.complex64)

    return samples, sequence


def main():
    args = parse_args()
    if args.fft_size < IQ_SAMPLES_PER_PACKET:
        raise ValueError("--fft-size must be at least 256")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(0.5)

    ring_size = max(args.fft_size * 4, args.scope_size * 4)
    ring = np.zeros(ring_size, dtype=np.complex64)
    state = {
        "write_pos": 0,
        "buffered": 0,
        "last_seq": None,
        "total_packets": 0,
        "lost_packets": 0,
        "interval_packets": 0,
        "interval_samples": 0,
        "running": True,
    }
    lock = threading.Lock()
    window = np.hanning(args.fft_size).astype(np.float32)
    freqs = np.fft.fftshift(np.fft.fftfreq(args.fft_size,
                                           d=1.0 / args.sample_rate)) / 1000.0
    scope_t = np.arange(args.scope_size) / args.sample_rate * 1000.0

    last_rate_time = time.time()

    def receiver_thread():
        while state["running"]:
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            samples, seq = parse_packet(data)
            if samples is None:
                continue

            with lock:
                if seq is not None:
                    if state["last_seq"] is not None:
                        expected = (state["last_seq"] + 1) & 0xFFFFFFFF
                        if seq != expected:
                            gap = (seq - expected) & 0xFFFFFFFF
                            if gap < 1000000:
                                state["lost_packets"] += gap
                    state["last_seq"] = seq

                write_pos = state["write_pos"]
                sample_count = len(samples)
                end_pos = write_pos + sample_count
                if end_pos <= ring_size:
                    ring[write_pos:end_pos] = samples
                else:
                    first_part = ring_size - write_pos
                    ring[write_pos:] = samples[:first_part]
                    ring[:end_pos - ring_size] = samples[first_part:]

                state["write_pos"] = end_pos % ring_size
                state["buffered"] = min(state["buffered"] + sample_count,
                                        ring_size)
                state["total_packets"] += 1
                state["interval_packets"] += 1
                state["interval_samples"] += sample_count

    def copy_latest(sample_count, reset_interval=False):
        with lock:
            if state["buffered"] < sample_count:
                return None

            write_pos = state["write_pos"]
            start = (write_pos - sample_count) % ring_size
            if start + sample_count <= ring_size:
                data = ring[start:start + sample_count].copy()
            else:
                first_part = ring_size - start
                data = np.empty(sample_count, dtype=np.float32)
                data[:first_part] = ring[start:]
                data[first_part:] = ring[:sample_count - first_part]

            stats = {
                "total_packets": state["total_packets"],
                "lost_packets": state["lost_packets"],
                "interval_packets": state["interval_packets"],
                "interval_samples": state["interval_samples"],
            }
            if reset_interval:
                state["interval_packets"] = 0
                state["interval_samples"] = 0
            return data, stats

    rx_thread = threading.Thread(target=receiver_thread, daemon=True)
    rx_thread.start()

    plt.ion()
    fig, (scope_ax, fft_ax) = plt.subplots(2, 1, figsize=(9, 7))

    (scope_i_line,) = scope_ax.plot(scope_t, np.zeros(args.scope_size), label="I")
    (scope_q_line,) = scope_ax.plot(scope_t, np.zeros(args.scope_size), label="Q")
    scope_ax.set_title("HF SDR DDC IQ Time Domain")
    scope_ax.set_xlabel("Time (ms)")
    scope_ax.set_ylabel("IQ code")
    scope_ax.set_xlim(scope_t[0], scope_t[-1])
    scope_ax.set_ylim(-2048, 2048)
    scope_ax.legend(loc="upper right")
    scope_ax.grid(True, alpha=0.25)

    (fft_line,) = fft_ax.plot(freqs, np.full_like(freqs, -140.0))
    fft_ax.set_title("HF SDR DDC IQ Spectrum")
    fft_ax.set_xlabel("Baseband Frequency (kHz)")
    fft_ax.set_ylabel("Magnitude (dBFS)")
    fft_ax.set_xlim(-args.sample_rate / 2000.0, args.sample_rate / 2000.0)
    fft_ax.set_ylim(args.ref_level - args.range_db, args.ref_level)
    fft_ax.grid(True, alpha=0.25)
    status = fft_ax.text(0.01, 0.98, "", transform=fft_ax.transAxes,
                         va="top", ha="left")
    fig.tight_layout()

    print(f"Listening on UDP 0.0.0.0:{args.port}")
    print(f"FFT size={args.fft_size} IQ sample_rate={args.sample_rate:.2f} S/s")
    print(f"Center frequency={args.center_frequency / 1_000_000.0:.6f} MHz")

    try:
        while plt.fignum_exists(fig.number):
            fft_snapshot = copy_latest(args.fft_size, reset_interval=True)
            scope_snapshot = copy_latest(args.scope_size)

            if scope_snapshot is not None:
                scope_block, _scope_stats = scope_snapshot
                scope_i = np.real(scope_block)
                scope_q = np.imag(scope_block)
                scope_i_line.set_ydata(scope_i)
                scope_q_line.set_ydata(scope_q)
                scope_peak = max(float(np.max(np.abs(scope_i))),
                                 float(np.max(np.abs(scope_q))))
                scope_limit = max(512.0, scope_peak * 1.25)
                scope_ax.set_ylim(-scope_limit, scope_limit)

            if fft_snapshot is not None:
                block, stats = fft_snapshot
                now = time.time()
                elapsed = now - last_rate_time
                sample_rate_est = stats["interval_samples"] / elapsed if elapsed > 0 else 0.0
                last_rate_time = now

                block -= np.mean(block)
                spectrum = np.fft.fftshift(np.fft.fft(block * window))
                magnitude = 20.0 * np.log10(
                    np.abs(spectrum) / (args.fft_size * 32768.0) + 1e-12
                )

                fft_line.set_ydata(magnitude)
                peak_bin = int(np.argmax(magnitude))
                peak_freq = freqs[peak_bin]
                peak_rf = args.center_frequency + peak_freq * 1000.0
                peak_db = magnitude[peak_bin]

                status.set_text(
                    f"packets={stats['total_packets']} lost={stats['lost_packets']}\n"
                    f"rate={sample_rate_est:.0f} IQ/s\n"
                    f"peak={peak_freq:.1f} kHz {peak_db:.1f} dBFS\n"
                    f"RF={peak_rf / 1_000_000.0:.6f} MHz"
                )

            plt.pause(args.update_ms / 1000.0)
    finally:
        state["running"] = False
        sock.close()


if __name__ == "__main__":
    main()
