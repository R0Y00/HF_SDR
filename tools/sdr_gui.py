import argparse
import socket
import struct
import sys
import threading
import time

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets


UDP_PORT = 5001
CTRL_PORT = 5002
SOCKET_BUFFER_BYTES = 8 * 1024 * 1024
DEFAULT_SAMPLE_RATE = 65_000_000.0 / 256.0
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
    parser = argparse.ArgumentParser(description="HF SDR PySide6 receiver GUI")
    parser.add_argument("--port", type=int, default=UDP_PORT,
                        help="UDP data listen port")
    parser.add_argument("--ctrl-port", type=int, default=CTRL_PORT,
                        help="board UDP tuning control port")
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE,
                        help="DDC IQ sample rate in samples per second")
    parser.add_argument("--center-frequency", type=float,
                        default=DEFAULT_CENTER_FREQ,
                        help="initial RF center frequency in Hz")
    parser.add_argument("--fft-size", type=int, default=16384,
                        help="FFT size")
    parser.add_argument("--scope-size", type=int, default=2048,
                        help="time-domain sample count")
    parser.add_argument("--update-ms", type=int, default=100,
                        help="display update interval")
    parser.add_argument("--ref-level", type=float, default=0.0,
                        help="spectrum top level in dBFS")
    parser.add_argument("--range-db", type=float, default=120.0,
                        help="spectrum display range in dB")
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
        return samples.astype(np.complex64), sequence, center_frequency, sample_rate

    words = words[:sample_count].astype(np.float32)
    return words.astype(np.complex64), sequence, center_frequency, sample_rate


class SharedStream:
    def __init__(self, sample_rate, center_frequency, fft_size, scope_size):
        self.lock = threading.Lock()
        self.snapshot_size = max(fft_size, scope_size)
        self.ring_size = self.snapshot_size * 4
        self.ring = np.zeros(self.ring_size, dtype=np.complex64)
        self.write_pos = 0
        self.buffered = 0
        self.last_seq = None
        self.total_packets = 0
        self.lost_packets = 0
        self.interval_packets = 0
        self.interval_samples = 0
        self.interval_lost = 0
        self.board_ip = "?"
        self.center_frequency = center_frequency
        self.sample_rate = sample_rate
        self.status_text = "waiting for data"
        self.running = True

    def add_packet(self, samples, seq, center_hz, sample_rate_hz, board_ip):
        with self.lock:
            self.board_ip = board_ip
            if center_hz is not None:
                self.center_frequency = center_hz
            if sample_rate_hz is not None and sample_rate_hz > 0:
                self.sample_rate = sample_rate_hz

            if seq is not None:
                if self.last_seq is not None:
                    expected = (self.last_seq + 1) & 0xFFFFFFFF
                    if seq != expected:
                        gap = (seq - expected) & 0xFFFFFFFF
                        if gap < 1_000_000:
                            self.lost_packets += gap
                            self.interval_lost += gap
                self.last_seq = seq

            count = len(samples)
            end_pos = self.write_pos + count
            if end_pos <= self.ring_size:
                self.ring[self.write_pos:end_pos] = samples
            else:
                first = self.ring_size - self.write_pos
                self.ring[self.write_pos:] = samples[:first]
                self.ring[:end_pos - self.ring_size] = samples[first:]

            self.write_pos = end_pos % self.ring_size
            self.buffered = min(self.buffered + count, self.ring_size)
            self.total_packets += 1
            self.interval_packets += 1
            self.interval_samples += count

    def snapshot(self, sample_count, reset_interval=False):
        with self.lock:
            if self.buffered < sample_count:
                return None

            start = (self.write_pos - sample_count) % self.ring_size
            if start + sample_count <= self.ring_size:
                data = self.ring[start:start + sample_count].copy()
            else:
                first = self.ring_size - start
                data = np.empty(sample_count, dtype=np.complex64)
                data[:first] = self.ring[start:]
                data[first:] = self.ring[:sample_count - first]

            stats = {
                "total_packets": self.total_packets,
                "lost_packets": self.lost_packets,
                "interval_packets": self.interval_packets,
                "interval_samples": self.interval_samples,
                "interval_lost": self.interval_lost,
                "board_ip": self.board_ip,
                "center_frequency": self.center_frequency,
                "sample_rate": self.sample_rate,
                "status_text": self.status_text,
            }

            if reset_interval:
                self.interval_packets = 0
                self.interval_samples = 0
                self.interval_lost = 0

            return data, stats

    def set_status(self, text):
        with self.lock:
            self.status_text = text


class UdpReceiver(threading.Thread):
    def __init__(self, shared, port):
        super().__init__(daemon=True)
        self.shared = shared
        self.port = port
        self.sock = None

    def run(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF,
                             SOCKET_BUFFER_BYTES)
        self.sock.bind(("0.0.0.0", self.port))
        self.sock.settimeout(0.5)
        self.shared.set_status(f"listening on UDP {self.port}")

        while self.shared.running:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            samples, seq, center_hz, sample_rate_hz = parse_packet(data)
            if samples is not None:
                self.shared.add_packet(samples, seq, center_hz,
                                       sample_rate_hz, addr[0])

    def close(self):
        if self.sock is not None:
            self.sock.close()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, args, shared):
        super().__init__()
        self.args = args
        self.shared = shared
        self.fft_size = args.fft_size
        self.scope_size = args.scope_size
        self.last_rate_time = time.time()
        self.window = np.kaiser(self.fft_size, 12.0).astype(np.float32)
        self.window_gain = float(np.sum(self.window))
        self.freqs = np.fft.fftshift(np.fft.fftfreq(
            self.fft_size, d=1.0 / args.sample_rate)) / 1000.0
        self.scope_t = np.arange(self.scope_size) / args.sample_rate * 1000.0

        self.setWindowTitle("HF SDR Receiver")
        self.resize(1050, 760)
        self._build_ui()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_plots)
        self.timer.start(args.update_ms)

    def _build_ui(self):
        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)

        controls = QtWidgets.QHBoxLayout()
        self.freq_edit = QtWidgets.QLineEdit("5.0M")
        self.freq_edit.setMaximumWidth(160)
        self.freq_edit.returnPressed.connect(self.tune)
        tune_button = QtWidgets.QPushButton("Tune")
        tune_button.clicked.connect(self.tune)
        self.board_label = QtWidgets.QLabel("Board: ?")
        self.center_label = QtWidgets.QLabel("Center: 5.000000 MHz")
        self.rate_label = QtWidgets.QLabel("Rate: 0 IQ/s")
        self.status_label = QtWidgets.QLabel("Waiting")

        controls.addWidget(QtWidgets.QLabel("Center"))
        controls.addWidget(self.freq_edit)
        controls.addWidget(tune_button)
        controls.addSpacing(20)
        controls.addWidget(self.board_label)
        controls.addWidget(self.center_label)
        controls.addWidget(self.rate_label)
        controls.addStretch(1)
        layout.addLayout(controls)
        layout.addWidget(self.status_label)

        pg.setConfigOptions(antialias=False)
        self.scope_plot = pg.PlotWidget(title="HF SDR DDC IQ Time Domain")
        self.scope_plot.setLabel("bottom", "Time", units="ms")
        self.scope_plot.setLabel("left", "IQ code")
        self.scope_plot.showGrid(x=True, y=True, alpha=0.25)
        self.scope_plot.addLegend()
        self.scope_i_curve = self.scope_plot.plot(
            self.scope_t, np.zeros(self.scope_size), pen=pg.mkPen("#1f77b4"),
            name="I")
        self.scope_q_curve = self.scope_plot.plot(
            self.scope_t, np.zeros(self.scope_size), pen=pg.mkPen("#ff7f0e"),
            name="Q")

        self.fft_plot = pg.PlotWidget(title="HF SDR DDC IQ Spectrum")
        self.fft_plot.setLabel("bottom", "Baseband Frequency", units="kHz")
        self.fft_plot.setLabel("left", "Magnitude", units="dBFS")
        self.fft_plot.setXRange(-self.args.sample_rate / 2000.0,
                                self.args.sample_rate / 2000.0)
        self.fft_plot.setYRange(self.args.ref_level - self.args.range_db,
                                self.args.ref_level)
        self.fft_plot.showGrid(x=True, y=True, alpha=0.25)
        self.fft_curve = self.fft_plot.plot(
            self.freqs, np.full_like(self.freqs, -140.0),
            pen=pg.mkPen("#1f77b4"))

        layout.addWidget(self.scope_plot, 1)
        layout.addWidget(self.fft_plot, 1)
        self.setCentralWidget(root)

    def tune(self):
        text = self.freq_edit.text().strip()
        try:
            freq_hz = parse_frequency(text)
        except ValueError:
            self.status_label.setText(f"Bad frequency: {text}")
            return

        snap = self.shared.snapshot(1)
        board_ip = snap[1]["board_ip"] if snap is not None else "?"
        if board_ip == "?":
            self.status_label.setText("Waiting for board IP from UDP data")
            return

        threading.Thread(target=self._send_tune,
                         args=(board_ip, freq_hz),
                         daemon=True).start()

    def _send_tune(self, board_ip, freq_hz):
        message = f"FREQ {freq_hz}\n".encode("ascii")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        try:
            sock.sendto(message, (board_ip, self.args.ctrl_port))
            try:
                reply, addr = sock.recvfrom(1024)
                reply_text = reply.decode("ascii", errors="replace").strip()
                text = (f"tuned {freq_hz / 1_000_000.0:.6f} MHz: "
                        f"{addr[0]} {reply_text}")
            except socket.timeout:
                text = f"tune sent to {board_ip}, no reply"
        finally:
            sock.close()

        self.shared.set_status(text)

    def update_plots(self):
        snap = self.shared.snapshot(max(self.fft_size, self.scope_size),
                                    reset_interval=True)
        if snap is None:
            self.status_label.setText(self.shared.status_text)
            return

        samples, stats = snap
        scope = samples[-self.scope_size:]
        scope_i = np.real(scope)
        scope_q = np.imag(scope)
        self.scope_i_curve.setData(self.scope_t, scope_i)
        self.scope_q_curve.setData(self.scope_t, scope_q)
        peak_scope = max(float(np.max(np.abs(scope_i))),
                         float(np.max(np.abs(scope_q))), 512.0)
        self.scope_plot.setYRange(-peak_scope * 1.25, peak_scope * 1.25,
                                  padding=0)

        now = time.time()
        elapsed = now - self.last_rate_time
        sample_rate_est = stats["interval_samples"] / elapsed if elapsed > 0 else 0.0
        self.last_rate_time = now

        block = samples[-self.fft_size:].copy()
        block -= np.mean(block)
        spectrum = np.fft.fftshift(np.fft.fft(block * self.window))
        power = (np.abs(spectrum) / (self.window_gain * 32768.0)) ** 2
        magnitude = 10.0 * np.log10(power + 1e-24)
        self.fft_curve.setData(self.freqs, magnitude)

        peak_bin = int(np.argmax(magnitude))
        peak_freq_khz = float(self.freqs[peak_bin])
        peak_db = float(magnitude[peak_bin])
        center_hz = float(stats["center_frequency"])
        peak_rf_mhz = (center_hz + peak_freq_khz * 1000.0) / 1_000_000.0

        self.board_label.setText(f"Board: {stats['board_ip']}")
        self.center_label.setText(f"Center: {center_hz / 1_000_000.0:.6f} MHz")
        self.rate_label.setText(f"Rate: {sample_rate_est:.0f} IQ/s")
        self.status_label.setText(
            f"packets={stats['total_packets']} lost={stats['lost_packets']} "
            f"win_lost={stats['interval_lost']} "
            f"peak={peak_freq_khz:.1f} kHz {peak_db:.1f} dBFS "
            f"RF={peak_rf_mhz:.6f} MHz | {stats['status_text']}"
        )

    def closeEvent(self, event):
        self.shared.running = False
        super().closeEvent(event)


def main():
    args = parse_args()
    if args.fft_size < IQ_SAMPLES_PER_PACKET:
        raise SystemExit("--fft-size must be at least 256")

    app = QtWidgets.QApplication(sys.argv)
    shared = SharedStream(args.sample_rate, args.center_frequency,
                          args.fft_size, args.scope_size)
    receiver = UdpReceiver(shared, args.port)
    receiver.start()

    window = MainWindow(args, shared)
    window.show()
    try:
        return app.exec()
    finally:
        shared.running = False
        receiver.close()


if __name__ == "__main__":
    raise SystemExit(main())
