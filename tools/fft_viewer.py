import argparse
import socket
import struct
import threading
import time

import matplotlib.pyplot as plt
import numpy as np


UDP_PORT = 5001
CTRL_PORT = 5002
SOCKET_BUFFER_BYTES = 8 * 1024 * 1024
DEFAULT_SAMPLE_RATE = 65_000_000.0 / 64.0
DEFAULT_CENTER_FREQ = 5_000_000.0
WORDS_PER_PACKET = 512
IQ_SAMPLES_PER_PACKET = 256
BYTES_PER_SAMPLE = 2
PAYLOAD_BYTES = WORDS_PER_PACKET * BYTES_PER_SAMPLE
HEADER_FORMAT = "<4sIHHHH"
HEADER_BYTES = struct.calcsize(HEADER_FORMAT)
HEADER_V2_FORMAT = "<4sIHHHHII"
HEADER_V2_BYTES = struct.calcsize(HEADER_V2_FORMAT)
MAGIC = b"HFSR"


def parse_args():
    parser = argparse.ArgumentParser(description="HF SDR live FFT viewer")
    parser.add_argument("--port", type=int, default=UDP_PORT,
                        help="UDP listen port")
    parser.add_argument("--ctrl-port", type=int, default=CTRL_PORT,
                        help="board UDP tuning control port")
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
    parser.add_argument("--tone-frequency", type=float, default=None,
                        help="optional RF test tone frequency in Hz for IRR display")
    parser.add_argument("--window", choices=("hann", "blackman", "kaiser"),
                        default="kaiser",
                        help="FFT window type")
    return parser.parse_args()


def parse_frequency(value):
    text = str(value).strip().lower()
    if text.endswith("mhz"):
        return int(round(float(text[:-3]) * 1_000_000.0))
    if text.endswith("m"):
        return int(round(float(text[:-1]) * 1_000_000.0))
    if text.endswith("khz"):
        return int(round(float(text[:-3]) * 1_000.0))
    if text.endswith("k"):
        return int(round(float(text[:-1]) * 1_000.0))
    return int(round(float(text)))


def parse_packet(data):
    payload = data
    sequence = None
    sample_format = 1
    sample_count = WORDS_PER_PACKET
    center_frequency = None
    sample_rate = None

    if len(data) >= HEADER_BYTES:
        magic, seq, header_bytes, sample_count, sample_format, payload_bytes = (
            struct.unpack_from(HEADER_FORMAT, data, 0)
        )

        if magic == MAGIC and header_bytes <= len(data):
            if sample_format not in (1, 2) or sample_count == 0:
                return None, None, None, None
            if header_bytes >= HEADER_V2_BYTES and len(data) >= HEADER_V2_BYTES:
                (_magic, _seq, _header_bytes, _sample_count, _sample_format,
                 _payload_bytes, center_hz, sample_rate_hz) = (
                    struct.unpack_from(HEADER_V2_FORMAT, data, 0)
                )
                center_frequency = float(center_hz)
                sample_rate = float(sample_rate_hz)
            payload = data[header_bytes:header_bytes + payload_bytes]
            sequence = seq

    if len(payload) < PAYLOAD_BYTES:
        return None, sequence, center_frequency, sample_rate

    words = np.frombuffer(payload[:PAYLOAD_BYTES], dtype="<i2")
    if sample_format == 2:
        words = words[:sample_count * 2].astype(np.float32)
        samples = words[0::2] + 1j * words[1::2]
        samples = samples.astype(np.complex64)
    else:
        words = words[:sample_count].astype(np.float32)
        samples = words.astype(np.complex64)

    return samples, sequence, center_frequency, sample_rate


def main():
    args = parse_args()
    if args.fft_size < IQ_SAMPLES_PER_PACKET:
        raise ValueError("--fft-size must be at least 256")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_BYTES)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(0.5)

    snapshot_size = max(args.fft_size, args.scope_size)
    ring_size = snapshot_size * 4
    ring = np.zeros(ring_size, dtype=np.complex64)
    state = {
        "write_pos": 0,
        "buffered": 0,
        "last_seq": None,
        "total_packets": 0,
        "lost_packets": 0,
        "interval_packets": 0,
        "interval_samples": 0,
        "interval_lost": 0,
        "center_frequency": args.center_frequency,
        "board_ip": "?",
        "tune_status": "type frequency in this terminal, e.g. 4.9M",
        "running": True,
    }
    lock = threading.Lock()
    if args.window == "blackman":
        window = np.blackman(args.fft_size).astype(np.float32)
    elif args.window == "kaiser":
        window = np.kaiser(args.fft_size, 12.0).astype(np.float32)
    else:
        window = np.hanning(args.fft_size).astype(np.float32)
    window_gain = float(np.sum(window))
    freqs = np.fft.fftshift(np.fft.fftfreq(args.fft_size,
                                           d=1.0 / args.sample_rate)) / 1000.0
    scope_t = np.arange(args.scope_size) / args.sample_rate * 1000.0

    last_rate_time = time.time()
    scope_peak_i = 0.0
    scope_peak_q = 0.0

    def receiver_thread():
        while state["running"]:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            samples, seq, center_hz, _sample_rate_hz = parse_packet(data)
            if samples is None:
                continue

            with lock:
                state["board_ip"] = addr[0]

                if center_hz is not None:
                    state["center_frequency"] = center_hz

                if seq is not None:
                    if state["last_seq"] is not None:
                        expected = (state["last_seq"] + 1) & 0xFFFFFFFF
                        if seq != expected:
                            gap = (seq - expected) & 0xFFFFFFFF
                            if gap < 1000000:
                                state["lost_packets"] += gap
                                state["interval_lost"] += gap
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
                data = np.empty(sample_count, dtype=np.complex64)
                data[:first_part] = ring[start:]
                data[first_part:] = ring[:sample_count - first_part]

            stats = {
                "total_packets": state["total_packets"],
                "lost_packets": state["lost_packets"],
                "interval_packets": state["interval_packets"],
                "interval_samples": state["interval_samples"],
                "interval_lost": state["interval_lost"],
                "center_frequency": state["center_frequency"],
                "board_ip": state["board_ip"],
                "tune_status": state["tune_status"],
            }
            if reset_interval:
                state["interval_packets"] = 0
                state["interval_samples"] = 0
                state["interval_lost"] = 0
            return data, stats

    rx_thread = threading.Thread(target=receiver_thread, daemon=True)
    rx_thread.start()

    def tune_thread():
        while state["running"]:
            try:
                text = input("Tune Hz/k/MHz> ").strip()
            except EOFError:
                return
            except OSError:
                return

            if not text:
                continue

            try:
                freq_hz = parse_frequency(text)
            except ValueError:
                with lock:
                    state["tune_status"] = f"bad frequency: {text}"
                print(f"bad frequency: {text}")
                continue

            with lock:
                board_ip = state["board_ip"]

            if board_ip == "?":
                with lock:
                    state["tune_status"] = "waiting for board IP from data stream"
                print("waiting for board IP from data stream")
                continue

            message = f"FREQ {freq_hz}\n".encode("ascii")
            ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            ctrl_sock.settimeout(1.0)
            try:
                ctrl_sock.sendto(message, (board_ip, args.ctrl_port))
                try:
                    reply, addr = ctrl_sock.recvfrom(1024)
                    reply_text = reply.decode("ascii", errors="replace").strip()
                    tune_status = (
                        f"tune {freq_hz / 1_000_000.0:.6f} MHz: "
                        f"{addr[0]} {reply_text}"
                    )
                except socket.timeout:
                    tune_status = f"tune sent to {board_ip}, no reply"
            finally:
                ctrl_sock.close()

            with lock:
                state["tune_status"] = tune_status
            print(tune_status)

    stdin_thread = threading.Thread(target=tune_thread, daemon=True)
    stdin_thread.start()

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
    plt.show(block=False)

    print(f"Listening on UDP 0.0.0.0:{args.port}")
    print(f"FFT size={args.fft_size} IQ sample_rate={args.sample_rate:.2f} S/s")
    print(f"Center frequency={args.center_frequency / 1_000_000.0:.6f} MHz")
    print("Type a frequency here and press Enter to tune: 4900000, 4900k, 4.9M")

    try:
        while plt.fignum_exists(fig.number):
            snapshot = copy_latest(snapshot_size, reset_interval=True)

            if snapshot is not None:
                snapshot_block, stats = snapshot
                scope_block = snapshot_block[-args.scope_size:]
                scope_i = np.real(scope_block)
                scope_q = np.imag(scope_block)
                scope_i_line.set_ydata(scope_i)
                scope_q_line.set_ydata(scope_q)
                scope_peak_i = float(np.max(np.abs(scope_i)))
                scope_peak_q = float(np.max(np.abs(scope_q)))
                scope_peak = max(scope_peak_i, scope_peak_q)
                scope_limit = max(512.0, scope_peak * 1.25)
                scope_ax.set_ylim(-scope_limit, scope_limit)

                block = snapshot_block[-args.fft_size:].copy()
                now = time.time()
                elapsed = now - last_rate_time
                sample_rate_est = stats["interval_samples"] / elapsed if elapsed > 0 else 0.0
                last_rate_time = now

                block -= np.mean(block)
                spectrum = np.fft.fftshift(np.fft.fft(block * window))
                power = (np.abs(spectrum) / (window_gain * 32768.0)) ** 2
                magnitude = 10.0 * np.log10(power + 1e-24)

                fft_line.set_ydata(magnitude)
                peak_bin = int(np.argmax(magnitude))
                peak_freq = freqs[peak_bin]
                center_frequency = stats["center_frequency"]
                peak_rf = center_frequency + peak_freq * 1000.0
                peak_db = magnitude[peak_bin]
                irr_text = ""

                if args.tone_frequency is not None:
                    expected_freq = (args.tone_frequency -
                                     center_frequency) / 1000.0
                    expected_bin = int(np.argmin(np.abs(freqs - expected_freq)))
                    image_bin = int(np.argmin(np.abs(freqs + expected_freq)))
                    expected_db = magnitude[expected_bin]
                    image_db = magnitude[image_bin]
                    irr = expected_db - image_db
                    irr_text = (
                        f"\nexp={expected_freq:.1f} kHz "
                        f"img={image_db:.1f} dBFS IRR={irr:.1f} dB"
                    )

                status.set_text(
                    f"packets={stats['total_packets']} lost={stats['lost_packets']}\n"
                    f"rate={sample_rate_est:.0f} IQ/s win_lost={stats['interval_lost']}\n"
                    f"board={stats['board_ip']}\n"
                    f"peak={peak_freq:.1f} kHz {peak_db:.1f} dBFS\n"
                    f"center={center_frequency / 1_000_000.0:.6f} MHz\n"
                    f"RF={peak_rf / 1_000_000.0:.6f} MHz\n"
                    f"Ipk={scope_peak_i:.0f} Qpk={scope_peak_q:.0f}\n"
                    f"{stats['tune_status']}"
                    f"{irr_text}"
                )

            fig.canvas.draw_idle()
            fig.canvas.flush_events()
            time.sleep(args.update_ms / 1000.0)
    finally:
        state["running"] = False
        sock.close()


if __name__ == "__main__":
    main()
