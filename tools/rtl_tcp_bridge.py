import argparse
import queue
import socket
import struct
import threading
import time

import numpy as np


DATA_PORT = 5001
CTRL_PORT = 5002
TCP_PORT = 1234
SOCKET_BUFFER_BYTES = 8 * 1024 * 1024
WORDS_PER_PACKET = 512
IQ_SAMPLES_PER_PACKET = 256
PAYLOAD_BYTES = WORDS_PER_PACKET * 2
MAGIC = b"HFSR"
HEADER_FORMAT = "<4sIHHHH"
HEADER_BYTES = struct.calcsize(HEADER_FORMAT)
HEADER_V2_FORMAT = "<4sIHHHHII"
HEADER_V2_BYTES = struct.calcsize(HEADER_V2_FORMAT)

RTL_TCP_MAGIC = b"RTL0"
RTL_TCP_TUNER_E4000 = 1
DEFAULT_CENTER_HZ = 5_000_000
DEFAULT_SAMPLE_RATE = 65_000_000 // 64


def parse_args():
    parser = argparse.ArgumentParser(description="HF SDR to rtl_tcp bridge")
    parser.add_argument("--data-port", type=int, default=DATA_PORT,
                        help="HF SDR UDP data port")
    parser.add_argument("--ctrl-port", type=int, default=CTRL_PORT,
                        help="HF SDR UDP tuning control port")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT,
                        help="local rtl_tcp-compatible TCP port")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="TCP bind address")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="int16 to uint8 scale multiplier")
    parser.add_argument("--queue", type=int, default=512,
                        help="maximum queued UDP packets")
    return parser.parse_args()


def parse_packet(data):
    if len(data) < HEADER_BYTES:
        return None

    magic, seq, header_bytes, sample_count, sample_format, payload_bytes = (
        struct.unpack_from(HEADER_FORMAT, data, 0)
    )
    if magic != MAGIC or sample_format != 2 or sample_count == 0:
        return None

    center_hz = None
    sample_rate_hz = None
    if header_bytes >= HEADER_V2_BYTES and len(data) >= HEADER_V2_BYTES:
        (_magic, _seq, _header_bytes, _sample_count, _sample_format,
         _payload_bytes, center_hz, sample_rate_hz) = (
            struct.unpack_from(HEADER_V2_FORMAT, data, 0)
        )

    payload = data[header_bytes:header_bytes + payload_bytes]
    if len(payload) < PAYLOAD_BYTES:
        return None

    words = np.frombuffer(payload[:PAYLOAD_BYTES], dtype="<i2")
    words = words[:sample_count * 2]
    return seq, words.copy(), center_hz, sample_rate_hz


def iq16_to_u8(words, scale):
    values = (words.astype(np.float32) * (scale / 256.0)) + 127.5
    return np.clip(values, 0, 255).astype(np.uint8).tobytes()


class BridgeState:
    def __init__(self, max_packets):
        self.queue = queue.Queue(maxsize=max_packets)
        self.lock = threading.Lock()
        self.board_ip = None
        self.center_hz = DEFAULT_CENTER_HZ
        self.sample_rate_hz = DEFAULT_SAMPLE_RATE
        self.last_seq = None
        self.lost = 0
        self.packets = 0
        self.running = True

    def put_packet(self, addr, parsed):
        seq, words, center_hz, sample_rate_hz = parsed
        with self.lock:
            self.board_ip = addr[0]
            if center_hz:
                self.center_hz = center_hz
            if sample_rate_hz:
                self.sample_rate_hz = sample_rate_hz
            if self.last_seq is not None:
                expected = (self.last_seq + 1) & 0xFFFFFFFF
                if seq != expected:
                    gap = (seq - expected) & 0xFFFFFFFF
                    if gap < 1_000_000:
                        self.lost += gap
            self.last_seq = seq
            self.packets += 1

        try:
            self.queue.put_nowait(words)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.queue.put_nowait(words)

    def snapshot(self):
        with self.lock:
            return {
                "board_ip": self.board_ip,
                "center_hz": self.center_hz,
                "sample_rate_hz": self.sample_rate_hz,
                "packets": self.packets,
                "lost": self.lost,
            }


class UdpThread(threading.Thread):
    def __init__(self, state, data_port):
        super().__init__(daemon=True)
        self.state = state
        self.data_port = data_port
        self.sock = None

    def run(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF,
                             SOCKET_BUFFER_BYTES)
        self.sock.bind(("0.0.0.0", self.data_port))
        self.sock.settimeout(0.5)
        print(f"HF SDR UDP listening on 0.0.0.0:{self.data_port}")

        while self.state.running:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            parsed = parse_packet(data)
            if parsed is not None:
                self.state.put_packet(addr, parsed)

    def close(self):
        if self.sock is not None:
            self.sock.close()


def send_tune(state, ctrl_port, freq_hz):
    snap = state.snapshot()
    board_ip = snap["board_ip"]
    if not board_ip:
        print("tune ignored: no board IP yet")
        return

    message = f"FREQ {freq_hz}\n".encode("ascii")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    try:
        sock.sendto(message, (board_ip, ctrl_port))
        try:
            reply, addr = sock.recvfrom(1024)
            text = reply.decode("ascii", errors="replace").strip()
            print(f"tune {freq_hz} Hz -> {addr[0]}:{addr[1]} {text}")
        except socket.timeout:
            print(f"tune {freq_hz} Hz sent to {board_ip}, no reply")
    finally:
        sock.close()


def handle_command(state, ctrl_port, command, value):
    if command == 0x01:
        send_tune(state, ctrl_port, value)
    elif command == 0x02:
        print(f"client requested sample_rate={value}; fixed by PL for now")
    elif command in (0x03, 0x04, 0x05, 0x08, 0x09, 0x0d):
        print(f"client command 0x{command:02x} value={value} accepted/ignored")
    else:
        print(f"client command 0x{command:02x} value={value} ignored")


def command_thread(state, ctrl_port, conn):
    while state.running:
        try:
            raw = conn.recv(5)
        except OSError:
            return
        if not raw:
            return
        if len(raw) < 5:
            continue
        command = raw[0]
        value = struct.unpack(">I", raw[1:5])[0]
        handle_command(state, ctrl_port, command, value)


def stream_client(state, args, conn, addr):
    print(f"rtl_tcp client connected from {addr[0]}:{addr[1]}")
    header = RTL_TCP_MAGIC + struct.pack(">II", RTL_TCP_TUNER_E4000, 0)
    conn.sendall(header)

    commands = threading.Thread(target=command_thread,
                                args=(state, args.ctrl_port, conn),
                                daemon=True)
    commands.start()

    last_print = time.time()
    sent_packets = 0
    try:
        while state.running:
            try:
                words = state.queue.get(timeout=0.5)
            except queue.Empty:
                continue

            data = iq16_to_u8(words, args.scale)
            conn.sendall(data)
            sent_packets += 1

            now = time.time()
            if now - last_print >= 5.0:
                snap = state.snapshot()
                print(f"stream packets={sent_packets} udp_packets={snap['packets']} "
                      f"lost={snap['lost']} board={snap['board_ip']} "
                      f"center={snap['center_hz']} sample_rate={snap['sample_rate_hz']}")
                last_print = now
    except (ConnectionError, OSError):
        print("rtl_tcp client disconnected")
    finally:
        try:
            conn.close()
        except OSError:
            pass


def main():
    args = parse_args()
    state = BridgeState(args.queue)
    udp = UdpThread(state, args.data_port)
    udp.start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.bind, args.tcp_port))
    server.listen(1)
    server.settimeout(0.5)
    print(f"rtl_tcp bridge listening on {args.bind}:{args.tcp_port}")
    print("Connect your SDR app to rtl_tcp 127.0.0.1:1234")

    try:
        while state.running:
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            stream_client(state, args, conn, addr)
    except KeyboardInterrupt:
        pass
    finally:
        state.running = False
        udp.close()
        server.close()


if __name__ == "__main__":
    main()
