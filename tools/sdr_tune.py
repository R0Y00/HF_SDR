import argparse
import socket
import struct


DATA_PORT = 5001
CTRL_PORT = 5002
MAGIC = b"HFSR"
HEADER_FORMAT = "<4sIHHHH"
HEADER_BYTES = struct.calcsize(HEADER_FORMAT)


def parse_args():
    parser = argparse.ArgumentParser(description="Tune the HF SDR DDC center frequency")
    parser.add_argument("frequency",
                        help="center frequency in Hz, kHz suffix, or MHz suffix")
    parser.add_argument("--board-ip", default=None,
                        help="board IP address; auto-detected from data UDP if omitted")
    parser.add_argument("--data-port", type=int, default=DATA_PORT,
                        help="UDP data port used for board auto-detection")
    parser.add_argument("--ctrl-port", type=int, default=CTRL_PORT,
                        help="board UDP control port")
    parser.add_argument("--timeout", type=float, default=1.0,
                        help="reply timeout in seconds")
    parser.add_argument("--discover-timeout", type=float, default=5.0,
                        help="board auto-detection timeout in seconds")
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


def discover_board_ip(data_port, timeout):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", data_port))
    sock.settimeout(timeout)

    try:
        while True:
            data, addr = sock.recvfrom(4096)
            if len(data) < HEADER_BYTES:
                continue

            magic, _seq, header_bytes, _sample_count, sample_format, _payload_bytes = (
                struct.unpack_from(HEADER_FORMAT, data, 0)
            )
            if magic == MAGIC and header_bytes >= HEADER_BYTES and sample_format in (1, 2):
                return addr[0]
    finally:
        sock.close()


def main():
    args = parse_args()
    freq_hz = parse_frequency(args.frequency)
    message = f"FREQ {freq_hz}\n".encode("ascii")
    board_ip = args.board_ip

    if board_ip is None:
        print(f"discovering board IP from UDP 0.0.0.0:{args.data_port} ...")
        try:
            board_ip = discover_board_ip(args.data_port, args.discover_timeout)
        except socket.timeout:
            raise SystemExit("no SDR data packet seen; start the board stream first")
        print(f"board IP: {board_ip}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(args.timeout)

    try:
        sock.sendto(message, (board_ip, args.ctrl_port))
        try:
            reply, addr = sock.recvfrom(1024)
            print(f"{addr[0]}:{addr[1]} {reply.decode('ascii', errors='replace').strip()}")
        except socket.timeout:
            print("sent, no reply")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
