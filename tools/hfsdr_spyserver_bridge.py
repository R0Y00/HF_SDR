import argparse
import queue
import socket
import struct
import threading
import time


DATA_PORT = 5001
CTRL_PORT = 5002
SPYSERVER_PORT = 5555
SOCKET_BUFFER_BYTES = 8 * 1024 * 1024

MAGIC = b"HFSR"
HEADER_FORMAT = "<4sIHHHHII"
HEADER_BYTES = struct.calcsize(HEADER_FORMAT)
PAYLOAD_BYTES = 1024
IQ_SAMPLES_PER_PACKET = 256
DEFAULT_SAMPLE_RATE_HZ = 65_000_000 // 64

SPYSERVER_PROTOCOL_VERSION = (2 << 24) | (0 << 16) | 1700

SPYSERVER_DEVICE_AIRSPY_HF = 2

SPYSERVER_CMD_HELLO = 0
SPYSERVER_CMD_SET_SETTING = 2
SPYSERVER_CMD_PING = 3

SPYSERVER_SETTING_STREAMING_MODE = 0
SPYSERVER_SETTING_STREAMING_ENABLED = 1
SPYSERVER_SETTING_IQ_FORMAT = 100
SPYSERVER_SETTING_IQ_FREQUENCY = 101
SPYSERVER_SETTING_IQ_DECIMATION = 102
SPYSERVER_SETTING_IQ_DIGITAL_GAIN = 103

SPYSERVER_STREAM_TYPE_STATUS = 0
SPYSERVER_STREAM_TYPE_IQ = 1

SPYSERVER_STREAM_FORMAT_INT16 = 2

SPYSERVER_MSG_TYPE_DEVICE_INFO = 0
SPYSERVER_MSG_TYPE_CLIENT_SYNC = 1
SPYSERVER_MSG_TYPE_PONG = 2
SPYSERVER_MSG_TYPE_INT16_IQ = 101


def parse_args():
    parser = argparse.ArgumentParser(
        description="HF SDR UDP int16 IQ to SpyServer bridge for SDR#"
    )
    parser.add_argument("--data-port", type=int, default=DATA_PORT,
                        help="HF SDR UDP data port")
    parser.add_argument("--ctrl-port", type=int, default=CTRL_PORT,
                        help="HF SDR UDP tuning control port")
    parser.add_argument("--spy-port", type=int, default=SPYSERVER_PORT,
                        help="local SpyServer-compatible TCP port")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="SpyServer bind address")
    parser.add_argument("--queue", type=int, default=512,
                        help="maximum queued UDP packets")
    parser.add_argument("--min-freq", type=int, default=0,
                        help="minimum tunable frequency in Hz")
    parser.add_argument("--max-freq", type=int, default=65_000_000,
                        help="maximum tunable frequency in Hz")
    return parser.parse_args()


def parse_hfsr_packet(data):
    if len(data) < HEADER_BYTES:
        return None

    magic, seq, header_bytes, sample_count, sample_format, payload_bytes, center_hz, sample_rate_hz = (
        struct.unpack_from(HEADER_FORMAT, data, 0)
    )
    if (magic != MAGIC or sample_format != 2 or
            sample_count != IQ_SAMPLES_PER_PACKET or
            payload_bytes != PAYLOAD_BYTES or
            header_bytes + payload_bytes > len(data)):
        return None

    payload = data[header_bytes:header_bytes + payload_bytes]
    return seq, payload, center_hz, sample_rate_hz


def recv_exact(conn, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = conn.recv(remaining)
        if not chunk:
            raise ConnectionError("client disconnected")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class BridgeState:
    def __init__(self, max_packets, min_freq, max_freq):
        self.queue = queue.Queue(maxsize=max_packets)
        self.lock = threading.Lock()
        self.board_ip = None
        self.center_hz = 4_900_000
        self.sample_rate_hz = DEFAULT_SAMPLE_RATE_HZ
        self.last_seq = None
        self.lost = 0
        self.packets = 0
        self.bad = 0
        self.running = True
        self.streaming_enabled = False
        self.spy_sequence = 0
        self.min_freq = min_freq
        self.max_freq = max_freq

    def put_packet(self, addr, parsed):
        seq, payload, center_hz, sample_rate_hz = parsed
        with self.lock:
            self.board_ip = addr[0]
            self.center_hz = center_hz or self.center_hz
            self.sample_rate_hz = sample_rate_hz or self.sample_rate_hz
            if self.last_seq is not None:
                expected = (self.last_seq + 1) & 0xFFFFFFFF
                if seq != expected:
                    gap = (seq - expected) & 0xFFFFFFFF
                    if gap < 1_000_000:
                        self.lost += gap
            self.last_seq = seq
            self.packets += 1

        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.queue.put_nowait(payload)

    def snapshot(self):
        with self.lock:
            return {
                "board_ip": self.board_ip,
                "center_hz": self.center_hz,
                "sample_rate_hz": self.sample_rate_hz,
                "packets": self.packets,
                "lost": self.lost,
                "bad": self.bad,
                "streaming_enabled": self.streaming_enabled,
            }

    def next_spy_sequence(self):
        with self.lock:
            seq = self.spy_sequence
            self.spy_sequence = (self.spy_sequence + 1) & 0xFFFFFFFF
            return seq


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

            parsed = parse_hfsr_packet(data)
            if parsed is None:
                with self.state.lock:
                    self.state.bad += 1
            else:
                self.state.put_packet(addr, parsed)

    def close(self):
        if self.sock is not None:
            self.sock.close()


def send_tune(state, ctrl_port, freq_hz):
    snap = state.snapshot()
    board_ip = snap["board_ip"]
    if not board_ip:
        print(f"tune {freq_hz} ignored: no board IP yet")
        return

    message = f"FREQ {freq_hz}\n".encode("ascii")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.5)
        sock.sendto(message, (board_ip, ctrl_port))
    with state.lock:
        state.center_hz = freq_hz
    print(f"tune {freq_hz} Hz -> {board_ip}:{ctrl_port}")


def spy_header(message_type, stream_type, sequence, body):
    return struct.pack(
        "<IIIII",
        SPYSERVER_PROTOCOL_VERSION,
        message_type,
        stream_type,
        sequence,
        len(body),
    )


def send_spy_message(conn, state, message_type, stream_type, body):
    sequence = state.next_spy_sequence()
    conn.sendall(spy_header(message_type, stream_type, sequence, body) + body)


def send_device_info(conn, state):
    snap = state.snapshot()
    sample_rate = int(snap["sample_rate_hz"])
    body = struct.pack(
        "<IIIIIIIIIIII",
        SPYSERVER_DEVICE_AIRSPY_HF,
        0x48534652,             # "HFSR"
        sample_rate,
        sample_rate,
        1,                      # DecimationStageCount
        1,                      # GainStageCount
        0,                      # MaximumGainIndex
        state.min_freq,
        state.max_freq,
        16,                     # Resolution
        0,                      # MinimumIQDecimation
        SPYSERVER_STREAM_FORMAT_INT16,
    )
    send_spy_message(conn, state, SPYSERVER_MSG_TYPE_DEVICE_INFO,
                     SPYSERVER_STREAM_TYPE_STATUS, body)


def send_client_sync(conn, state):
    snap = state.snapshot()
    center = int(snap["center_hz"])
    body = struct.pack(
        "<IIIIIIIII",
        1,                      # CanControl
        0,                      # Gain
        center,
        center,
        center,
        state.min_freq,
        state.max_freq,
        state.min_freq,
        state.max_freq,
    )
    send_spy_message(conn, state, SPYSERVER_MSG_TYPE_CLIENT_SYNC,
                     SPYSERVER_STREAM_TYPE_STATUS, body)


def handle_command(state, ctrl_port, conn, command, body):
    if command == SPYSERVER_CMD_HELLO:
        send_device_info(conn, state)
        send_client_sync(conn, state)
        return

    if command == SPYSERVER_CMD_PING:
        send_spy_message(conn, state, SPYSERVER_MSG_TYPE_PONG,
                         SPYSERVER_STREAM_TYPE_STATUS, b"")
        return

    if command != SPYSERVER_CMD_SET_SETTING or len(body) < 8:
        return

    setting, value = struct.unpack_from("<II", body, 0)
    if setting == SPYSERVER_SETTING_STREAMING_ENABLED:
        with state.lock:
            state.streaming_enabled = value != 0
        print(f"spy streaming_enabled={value != 0}")
    elif setting == SPYSERVER_SETTING_IQ_FREQUENCY:
        send_tune(state, ctrl_port, value)
        send_client_sync(conn, state)
    elif setting == SPYSERVER_SETTING_IQ_FORMAT:
        print(f"spy requested IQ format={value}")
    elif setting == SPYSERVER_SETTING_STREAMING_MODE:
        print(f"spy requested streaming mode={value}")
    elif setting in (SPYSERVER_SETTING_IQ_DECIMATION,
                     SPYSERVER_SETTING_IQ_DIGITAL_GAIN):
        print(f"spy setting {setting}={value} accepted/ignored")
    else:
        print(f"spy setting {setting}={value} ignored")


def command_thread(state, ctrl_port, conn):
    while state.running:
        try:
            header = recv_exact(conn, 8)
            command, body_size = struct.unpack("<II", header)
            if body_size > 256:
                raise ConnectionError("oversized command")
            body = recv_exact(conn, body_size) if body_size else b""
            handle_command(state, ctrl_port, conn, command, body)
        except (ConnectionError, OSError, struct.error):
            with state.lock:
                state.streaming_enabled = False
            return


def stream_client(state, args, conn, addr):
    print(f"SpyServer client connected from {addr[0]}:{addr[1]}")
    commands = threading.Thread(target=command_thread,
                                args=(state, args.ctrl_port, conn),
                                daemon=True)
    commands.start()

    last_print = time.time()
    sent_packets = 0
    try:
        while state.running:
            snap = state.snapshot()
            if not snap["streaming_enabled"]:
                time.sleep(0.05)
                continue

            try:
                payload = state.queue.get(timeout=0.5)
            except queue.Empty:
                continue

            send_spy_message(conn, state, SPYSERVER_MSG_TYPE_INT16_IQ,
                             SPYSERVER_STREAM_TYPE_IQ, payload)
            sent_packets += 1

            now = time.time()
            if now - last_print >= 5.0:
                snap = state.snapshot()
                print(f"spy packets={sent_packets} udp_packets={snap['packets']} "
                      f"lost={snap['lost']} bad={snap['bad']} board={snap['board_ip']} "
                      f"center={snap['center_hz']} sample_rate={snap['sample_rate_hz']}")
                last_print = now
    except (ConnectionError, OSError):
        print("SpyServer client disconnected")
    finally:
        with state.lock:
            state.streaming_enabled = False
        try:
            conn.close()
        except OSError:
            pass


def main():
    args = parse_args()
    state = BridgeState(args.queue, args.min_freq, args.max_freq)
    udp = UdpThread(state, args.data_port)
    udp.start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.bind, args.spy_port))
    server.listen(1)
    server.settimeout(0.5)
    print(f"HF SDR SpyServer bridge listening on {args.bind}:{args.spy_port}")
    print(f"Use SDR# Source: AIRSPY Server Network -> sdr://{args.bind}:{args.spy_port}")

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
