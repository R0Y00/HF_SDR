import argparse
import socket
import struct
import time
from pathlib import Path

import numpy as np


UDP_PORT = 5001
SOCKET_BUFFER_BYTES = 16 * 1024 * 1024
HEADER_FORMAT = "<4sIHHHH"
HEADER_BYTES = struct.calcsize(HEADER_FORMAT)
MAGIC = b"HFSR"
IQ_SAMPLES_PER_PACKET = 256
PAYLOAD_BYTES = 1024


def parse_args():
    parser = argparse.ArgumentParser(
        description="Capture IQ blocks when the FFT skirt near the test tone bulges"
    )
    parser.add_argument("--port", type=int, default=UDP_PORT)
    parser.add_argument("--sample-rate", type=float, default=65_000_000.0 / 256.0)
    parser.add_argument("--center-frequency", type=float, default=5_000_000.0)
    parser.add_argument("--tone-frequency", type=float, default=4_950_000.0)
    parser.add_argument("--fft-size", type=int, default=16384)
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--threshold-db", type=float, default=22.0,
                        help="trigger if peak is less than this many dB above skirt")
    parser.add_argument("--skirt-threshold-dbfs", type=float, default=-90.0,
                        help="trigger if the 95th percentile skirt rises above this dBFS level")
    parser.add_argument("--search-khz", type=float, default=30.0,
                        help="search half width around expected tone for the real peak")
    parser.add_argument("--min-peak-dbfs", type=float, default=-60.0,
                        help="ignore blocks whose local peak is below this level")
    parser.add_argument("--near-khz", type=float, default=18.0,
                        help="skirt measurement half width around tone")
    parser.add_argument("--exclude-khz", type=float, default=2.0,
                        help="exclude this half width around tone peak")
    parser.add_argument("--cooldown", type=float, default=2.0,
                        help="minimum seconds between captures")
    parser.add_argument("--max-captures", type=int, default=20)
    parser.add_argument("--out-dir", default="tools/captures")
    return parser.parse_args()


def parse_packet(data):
    if len(data) < HEADER_BYTES:
        return None

    magic, seq, header_bytes, sample_count, sample_format, payload_bytes = (
        struct.unpack_from(HEADER_FORMAT, data, 0)
    )
    if magic != MAGIC or sample_format != 2:
        return None
    if header_bytes > len(data) or sample_count != IQ_SAMPLES_PER_PACKET:
        return None

    payload = data[header_bytes:header_bytes + payload_bytes]
    if len(payload) < PAYLOAD_BYTES:
        return None

    words = np.frombuffer(payload[:PAYLOAD_BYTES], dtype="<i2")
    iq = words[0::2].astype(np.float32) + 1j * words[1::2].astype(np.float32)
    return seq, iq.astype(np.complex64)


def latest_block(ring, write_pos, count):
    size = len(ring)
    start = (write_pos - count) % size
    if start + count <= size:
        return ring[start:start + count].copy()
    first = size - start
    out = np.empty(count, dtype=np.complex64)
    out[:first] = ring[start:]
    out[first:] = ring[:count - first]
    return out


def measure_bulge(iq, sample_rate, expected_freq, search_hz, near_hz, exclude_hz):
    iq = iq - np.mean(iq)
    window = np.kaiser(len(iq), 12.0).astype(np.float32)
    window_gain = float(np.sum(window))
    spectrum = np.fft.fftshift(np.fft.fft(iq * window))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(iq), d=1.0 / sample_rate))
    mag = 20.0 * np.log10(np.abs(spectrum) / (window_gain * 32768.0) + 1e-12)

    search_mask = np.abs(freqs - expected_freq) <= search_hz
    if not np.any(search_mask):
        return None

    search_bins = np.flatnonzero(search_mask)
    peak_bin = int(search_bins[np.argmax(mag[search_mask])])
    peak_freq = float(freqs[peak_bin])
    peak_db = float(mag[peak_bin])
    near = np.abs(freqs - peak_freq) <= near_hz
    exclude = np.abs(freqs - peak_freq) <= exclude_hz
    skirt_mask = near & ~exclude

    if not np.any(skirt_mask):
        return None

    skirt_p95 = float(np.percentile(mag[skirt_mask], 95))
    skirt_max = float(np.max(mag[skirt_mask]))
    margin_p95 = peak_db - skirt_p95
    margin_max = peak_db - skirt_max

    return {
        "freqs": freqs,
        "mag": mag,
        "peak_freq": peak_freq,
        "peak_db": peak_db,
        "skirt_p95": skirt_p95,
        "skirt_max": skirt_max,
        "margin_p95": margin_p95,
        "margin_max": margin_max,
    }


def save_capture(out_dir, index, iq, metrics, first_seq, last_seq, lost):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = out_dir / f"bulge_{stamp}_{index:03d}"

    raw_words = np.empty(iq.size * 2, dtype="<i2")
    raw_words[0::2] = np.clip(np.real(iq), -32768, 32767).astype("<i2")
    raw_words[1::2] = np.clip(np.imag(iq), -32768, 32767).astype("<i2")
    raw_words.tofile(base.with_suffix(".iq16"))

    np.savez(
        base.with_suffix(".npz"),
        iq=iq,
        freqs=metrics["freqs"],
        mag=metrics["mag"],
        first_seq=np.uint32(first_seq if first_seq is not None else 0),
        last_seq=np.uint32(last_seq if last_seq is not None else 0),
        lost=np.uint64(lost),
        peak_freq=metrics["peak_freq"],
        peak_db=metrics["peak_db"],
        skirt_p95=metrics["skirt_p95"],
        skirt_max=metrics["skirt_max"],
        margin_p95=metrics["margin_p95"],
        margin_max=metrics["margin_max"],
    )
    return base


def main():
    args = parse_args()
    expected_freq = args.tone_frequency - args.center_frequency
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ring_size = args.fft_size * 4
    ring = np.zeros(ring_size, dtype=np.complex64)
    write_pos = 0
    buffered = 0
    last_seq = None
    total_packets = 0
    lost_packets = 0
    captures = 0
    last_capture_time = 0.0

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_BYTES)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(2.0)

    print(f"Listening on UDP 0.0.0.0:{args.port}")
    print(f"Expected baseband={expected_freq / 1000.0:.3f} kHz")
    print(f"Trigger: peak - skirt_p95 < {args.threshold_db:.1f} dB "
          f"or skirt_p95 > {args.skirt_threshold_dbfs:.1f} dBFS")
    print(f"Saving captures to {out_dir.resolve()}")

    start = time.time()
    last_report = start

    try:
        while time.time() - start < args.seconds and captures < args.max_captures:
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                print("timeout")
                continue

            parsed = parse_packet(data)
            if parsed is None:
                continue

            seq, iq = parsed
            total_packets += 1
            if last_seq is not None:
                expected_seq = (last_seq + 1) & 0xFFFFFFFF
                if seq != expected_seq:
                    gap = (seq - expected_seq) & 0xFFFFFFFF
                    if gap < 1_000_000:
                        lost_packets += gap
                        print(f"SEQ_JUMP expected={expected_seq} got={seq} lost+={gap}")
            last_seq = seq

            count = len(iq)
            end_pos = write_pos + count
            if end_pos <= ring_size:
                ring[write_pos:end_pos] = iq
            else:
                first = ring_size - write_pos
                ring[write_pos:] = iq[:first]
                ring[:end_pos - ring_size] = iq[first:]
            write_pos = end_pos % ring_size
            buffered = min(buffered + count, ring_size)

            if buffered < args.fft_size:
                continue

            now = time.time()
            if now - last_capture_time < args.cooldown:
                continue

            block = latest_block(ring, write_pos, args.fft_size)
            metrics = measure_bulge(
                block,
                args.sample_rate,
                expected_freq,
                args.search_khz * 1000.0,
                args.near_khz * 1000.0,
                args.exclude_khz * 1000.0,
            )
            if metrics is None:
                continue

            margin_trigger = metrics["margin_p95"] < args.threshold_db
            skirt_trigger = metrics["skirt_p95"] > args.skirt_threshold_dbfs
            if (metrics["peak_db"] >= args.min_peak_dbfs and
                    (margin_trigger or skirt_trigger)):
                captures += 1
                first_seq = (seq - (args.fft_size // IQ_SAMPLES_PER_PACKET) + 1) & 0xFFFFFFFF
                base = save_capture(out_dir, captures, block, metrics,
                                    first_seq, seq, lost_packets)
                last_capture_time = now
                print(
                    f"CAPTURE {captures}: {base.name} "
                    f"peak_freq={metrics['peak_freq'] / 1000.0:.2f} kHz "
                    f"peak={metrics['peak_db']:.1f} dBFS "
                    f"skirt95={metrics['skirt_p95']:.1f} dBFS "
                    f"skirtmax={metrics['skirt_max']:.1f} dBFS "
                    f"margin95={metrics['margin_p95']:.1f} dB "
                    f"reason={'margin' if margin_trigger else 'skirt'} "
                    f"packets={total_packets} lost={lost_packets}"
                )

            if now - last_report >= 5.0:
                print(
                    f"RUN packets={total_packets} lost={lost_packets} "
                    f"peak_freq={metrics['peak_freq'] / 1000.0:.2f} "
                    f"peak={metrics['peak_db']:.1f} "
                    f"skirt95={metrics['skirt_p95']:.1f} "
                    f"margin95={metrics['margin_p95']:.1f} "
                    f"captures={captures}"
                )
                last_report = now
    finally:
        sock.close()

    print(f"DONE packets={total_packets} lost={lost_packets} captures={captures}")


if __name__ == "__main__":
    main()
