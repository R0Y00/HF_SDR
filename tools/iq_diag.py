import argparse
import socket
import struct
import time

import numpy as np


UDP_PORT = 5001
SOCKET_BUFFER_BYTES = 16 * 1024 * 1024
WORDS_PER_PACKET = 512
IQ_SAMPLES_PER_PACKET = 256
PAYLOAD_BYTES = WORDS_PER_PACKET * 2
HEADER_FORMAT = "<4sIHHHH"
HEADER_BYTES = struct.calcsize(HEADER_FORMAT)
MAGIC = b"HFSR"


def parse_args():
    parser = argparse.ArgumentParser(description="HF SDR IQ packet diagnostic")
    parser.add_argument("--port", type=int, default=UDP_PORT)
    parser.add_argument("--sample-rate", type=float, default=65_000_000.0 / 256.0)
    parser.add_argument("--center-frequency", type=float, default=5_000_000.0)
    parser.add_argument("--tone-frequency", type=float, default=4_950_000.0,
                        help="expected RF test tone frequency in Hz")
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--block-packets", type=int, default=64,
                        help="packets per FFT diagnostic block")
    parser.add_argument("--save-bad", default=None,
                        help="optional .bin file for suspicious payloads")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def parse_packet(data):
    if len(data) < HEADER_BYTES:
        return None

    magic, seq, header_bytes, sample_count, sample_format, payload_bytes = (
        struct.unpack_from(HEADER_FORMAT, data, 0)
    )

    if magic != MAGIC or sample_format != 2:
        return None

    payload = data[header_bytes:header_bytes + payload_bytes]
    if len(payload) < PAYLOAD_BYTES or sample_count != IQ_SAMPLES_PER_PACKET:
        return None

    words = np.frombuffer(payload[:PAYLOAD_BYTES], dtype="<i2")
    i = words[0::2].astype(np.float32)
    q = words[1::2].astype(np.float32)
    return seq, payload[:PAYLOAD_BYTES], i, q


def rms(x):
    return float(np.sqrt(np.mean(x * x)))


def block_fft_metrics(iq, sample_rate, expected_baseband):
    iq = iq.astype(np.complex64)
    iq = iq - np.mean(iq)
    window = np.hanning(len(iq)).astype(np.float32)
    spectrum = np.fft.fftshift(np.fft.fft(iq * window))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(iq), d=1.0 / sample_rate))
    mag = 20.0 * np.log10(np.abs(spectrum) / (len(iq) * 32768.0) + 1e-12)

    peak_idx = int(np.argmax(mag))
    peak_freq = float(freqs[peak_idx])
    peak_db = float(mag[peak_idx])

    expected_idx = int(np.argmin(np.abs(freqs - expected_baseband)))
    image_idx = int(np.argmin(np.abs(freqs + expected_baseband)))
    expected_db = float(mag[expected_idx])
    image_db = float(mag[image_idx])
    dc_db = float(mag[int(np.argmin(np.abs(freqs)))])

    return {
        "peak_freq": peak_freq,
        "peak_db": peak_db,
        "expected_db": expected_db,
        "image_db": image_db,
        "image_rejection": expected_db - image_db,
        "dc_db": dc_db,
    }


def main():
    args = parse_args()
    expected_baseband = args.tone_frequency - args.center_frequency
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_BYTES)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(2.0)

    bad_file = open(args.save_bad, "ab") if args.save_bad else None

    start = time.time()
    last_report = start
    last_seq = None
    total_packets = 0
    lost_packets = 0
    bad_packets = 0
    q_zero_packets = 0
    imbalance_packets = 0
    block_i = []
    block_q = []

    print(f"Listening on UDP 0.0.0.0:{args.port}")
    print(f"Expected baseband: {expected_baseband / 1000.0:.3f} kHz")

    try:
        while time.time() - start < args.seconds:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                print("timeout")
                continue

            parsed = parse_packet(data)
            if parsed is None:
                continue

            seq, payload, i, q = parsed
            total_packets += 1

            if last_seq is not None:
                expected = (last_seq + 1) & 0xFFFFFFFF
                if seq != expected:
                    gap = (seq - expected) & 0xFFFFFFFF
                    if gap < 1_000_000:
                        lost_packets += gap
                        print(f"SEQ_JUMP expected={expected} got={seq} lost+={gap}")
            last_seq = seq

            i_pk = float(np.max(np.abs(i)))
            q_pk = float(np.max(np.abs(q)))
            i_rms = rms(i)
            q_rms = rms(q)
            q_zero_ratio = float(np.mean(q == 0.0))
            iq_corr = float(np.mean(i * q) / ((i_rms * q_rms) + 1e-12))

            suspicious = False
            reasons = []

            if q_pk < 8.0 or q_rms < 2.0 or q_zero_ratio > 0.5:
                q_zero_packets += 1
                suspicious = True
                reasons.append("Q_ZERO_OR_TINY")

            ratio = q_rms / (i_rms + 1e-12)
            if ratio < 0.25 or ratio > 4.0:
                imbalance_packets += 1
                suspicious = True
                reasons.append(f"IQ_RMS_RATIO={ratio:.3f}")

            if i_pk >= 32760.0 or q_pk >= 32760.0:
                suspicious = True
                reasons.append("CLIPPING")

            if suspicious:
                bad_packets += 1
                print(f"BAD seq={seq} from={addr[0]}:{addr[1]} "
                      f"Ipk={i_pk:.0f} Qpk={q_pk:.0f} "
                      f"Irms={i_rms:.1f} Qrms={q_rms:.1f} "
                      f"Qzero={q_zero_ratio:.2f} corr={iq_corr:.3f} "
                      f"{' '.join(reasons)}")
                if bad_file:
                    bad_file.write(payload)
            elif args.verbose:
                print(f"OK seq={seq} Ipk={i_pk:.0f} Qpk={q_pk:.0f} "
                      f"Irms={i_rms:.1f} Qrms={q_rms:.1f} corr={iq_corr:.3f}")

            block_i.append(i)
            block_q.append(q)
            if len(block_i) >= args.block_packets:
                bi = np.concatenate(block_i)
                bq = np.concatenate(block_q)
                metrics = block_fft_metrics(bi + 1j * bq,
                                            args.sample_rate,
                                            expected_baseband)
                print(f"FFT peak={metrics['peak_freq'] / 1000.0:.2f} kHz "
                      f"{metrics['peak_db']:.1f} dBFS "
                      f"expected={metrics['expected_db']:.1f} dBFS "
                      f"image={metrics['image_db']:.1f} dBFS "
                      f"IRR={metrics['image_rejection']:.1f} dB "
                      f"dc={metrics['dc_db']:.1f} dBFS")
                block_i.clear()
                block_q.clear()

            now = time.time()
            if now - last_report >= 5.0:
                print(f"SUMMARY packets={total_packets} lost={lost_packets} "
                      f"bad={bad_packets} q_zero={q_zero_packets} "
                      f"imbalance={imbalance_packets}")
                last_report = now

    finally:
        if bad_file:
            bad_file.close()

    print(f"DONE packets={total_packets} lost={lost_packets} "
          f"bad={bad_packets} q_zero={q_zero_packets} "
          f"imbalance={imbalance_packets}")


if __name__ == "__main__":
    main()
