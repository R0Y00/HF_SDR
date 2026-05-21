import socket
import struct
import time
import argparse


UDP_PORT = 5001
WORDS_PER_PACKET = 512
IQ_SAMPLES_PER_PACKET = 256
BYTES_PER_SAMPLE = 2
PAYLOAD_BYTES = WORDS_PER_PACKET * BYTES_PER_SAMPLE
HEADER_FORMAT = "<4sIHHHH"
HEADER_BYTES = struct.calcsize(HEADER_FORMAT)
MAGIC = b"HFSR"


def parse_args():
    parser = argparse.ArgumentParser(description="HF SDR UDP receiver")
    parser.add_argument("--port", type=int, default=UDP_PORT,
                        help="UDP listen port")
    parser.add_argument("--save", default=None,
                        help="save raw int16 ADC payload to this file")
    parser.add_argument("--quiet", action="store_true",
                        help="do not print every packet")
    return parser.parse_args()


def decode_samples(payload):
    sample_count = min(len(payload) // BYTES_PER_SAMPLE, WORDS_PER_PACKET)
    return struct.unpack("<" + "h" * sample_count,
                         payload[:sample_count * BYTES_PER_SAMPLE])


def main():
    args = parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(2.0)

    raw_file = open(args.save, "ab") if args.save else None

    print(f"Listening on UDP 0.0.0.0:{args.port}")
    if raw_file:
        print(f"Saving raw payload to {args.save}")

    count = 0
    data_packets = 0
    lost_packets = 0
    total_payload_bytes = 0
    interval_packets = 0
    interval_payload_bytes = 0
    interval_lost = 0
    last_seq = None
    last_time = time.time()

    try:
        while True:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                print("timeout: no packet")
                continue

            count += 1
            payload = data
            seq_text = "seq=?"
            sample_format = 1
            sample_count = WORDS_PER_PACKET

            if len(data) >= HEADER_BYTES:
                magic, seq, header_bytes, sample_count, sample_format, payload_bytes = (
                    struct.unpack_from(HEADER_FORMAT, data, 0)
                )
                if magic == MAGIC and header_bytes <= len(data):
                    payload = data[header_bytes:header_bytes + payload_bytes]
                    seq_text = f"seq={seq}"

                    if last_seq is not None:
                        expected = (last_seq + 1) & 0xFFFFFFFF
                        if seq != expected:
                            missed = (seq - expected) & 0xFFFFFFFF
                            lost_packets += missed
                            interval_lost += missed
                            print(f"sequence jump: expected={expected} got={seq} "
                                  f"lost+={missed}")

                    last_seq = seq

                    if sample_format == 1 and sample_count != WORDS_PER_PACKET:
                        print(f"sample_count warning: {sample_count}")
                    if sample_format == 2 and sample_count != IQ_SAMPLES_PER_PACKET:
                        print(f"iq sample_count warning: {sample_count}")
                    if sample_format not in (1, 2):
                        print(f"sample_format warning: {sample_format}")

            if len(payload) >= PAYLOAD_BYTES:
                samples = decode_samples(payload)
                data_packets += 1
                interval_packets += 1
                total_payload_bytes += len(payload)
                interval_payload_bytes += len(payload)

                if raw_file:
                    raw_file.write(payload[:PAYLOAD_BYTES])

                if not args.quiet:
                    head = " ".join(str(value) for value in samples[:16])
                    fmt_text = "IQ_S16" if sample_format == 2 else "S16"
                    print(f"pkt={count} {seq_text} from={addr[0]}:{addr[1]} "
                          f"fmt={fmt_text} len={len(data)} payload={len(payload)} "
                          f"min={min(samples)} max={max(samples)} "
                          f"avg={sum(samples) // len(samples)} first: {head}")
            else:
                print(f"pkt={count} from={addr[0]}:{addr[1]} len={len(data)}")

            now = time.time()
            elapsed = now - last_time
            if elapsed >= 5.0:
                payload_mbps = interval_payload_bytes * 8.0 / elapsed / 1_000_000.0
                samples_per_packet = (IQ_SAMPLES_PER_PACKET
                                      if sample_format == 2 else WORDS_PER_PACKET)
                sample_rate = interval_packets * samples_per_packet / elapsed
                print(f"rate={payload_mbps:.3f} Mbps "
                      f"sample_rate={sample_rate:.0f} S/s "
                      f"packets={interval_packets} lost={interval_lost} "
                      f"total_packets={data_packets} total_lost={lost_packets} "
                      f"total_payload={total_payload_bytes}")
                interval_packets = 0
                interval_payload_bytes = 0
                interval_lost = 0
                last_time = now
    finally:
        if raw_file:
            raw_file.close()


if __name__ == "__main__":
    main()
