import argparse
import socket
import struct
import sys
import threading
import time

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtMultimedia, QtWidgets


UDP_PORT = 5001
CTRL_PORT = 5002
SOCKET_BUFFER_BYTES = 8 * 1024 * 1024
DEFAULT_SAMPLE_RATE = 65_000_000.0 / 64.0
DEFAULT_CENTER_FREQ = 5_000_000.0
DEFAULT_AUDIO_RATE = 48_000
WORDS_PER_PACKET = 512
IQ_SAMPLES_PER_PACKET = 256
BYTES_PER_SAMPLE = 2
PAYLOAD_BYTES = WORDS_PER_PACKET * BYTES_PER_SAMPLE
HEADER_FORMAT = "<4sIHHHH"
HEADER_BYTES = struct.calcsize(HEADER_FORMAT)
HEADER_V2_FORMAT = "<4sIHHHHII"
HEADER_V2_BYTES = struct.calcsize(HEADER_V2_FORMAT)
MAGIC = b"HFSR"
BAND_PRESETS = [
    ("160m", "1.84M"),
    ("80m", "3.60M"),
    ("60m", "5.33M"),
    ("49m", "6.10M"),
    ("40m", "7.10M"),
    ("31m", "9.60M"),
    ("30m", "10.12M"),
    ("25m", "11.80M"),
    ("20m", "14.20M"),
    ("19m", "15.20M"),
    ("15m", "21.20M"),
    ("10m", "28.40M"),
]


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
    parser.add_argument("--adc-full-scale-vpp", type=float, default=10.0,
                        help="ADC full-scale input span in Vpp for voltage estimates")
    parser.add_argument("--level-hold", type=float, default=3.0,
                        help="seconds to hold peak measurement readouts")
    parser.add_argument("--audio-rate", type=int, default=DEFAULT_AUDIO_RATE,
                        help="audio output sample rate")
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
        self.audio_read_pos = 0
        self.audio_unread = 0
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
            self.audio_unread = min(self.audio_unread + count, self.ring_size)
            self.audio_read_pos = ((self.write_pos - self.audio_unread)
                                   % self.ring_size)
            self.total_packets += 1
            self.interval_packets += 1
            self.interval_samples += count

    def read_audio(self, max_count):
        with self.lock:
            count = min(int(max_count), self.audio_unread)
            if count <= 0:
                return None, self.sample_rate

            start = self.audio_read_pos
            if start + count <= self.ring_size:
                data = self.ring[start:start + count].copy()
            else:
                first = self.ring_size - start
                data = np.empty(count, dtype=np.complex64)
                data[:first] = self.ring[start:]
                data[first:] = self.ring[:count - first]

            self.audio_read_pos = (self.audio_read_pos + count) % self.ring_size
            self.audio_unread -= count
            return data, self.sample_rate

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
        self.display_sample_rate = args.sample_rate
        self.freqs = self._make_freqs(self.display_sample_rate)
        self.scope_t = self._make_scope_t(self.display_sample_rate)
        self.hold_until = 0.0
        self.hold_peak_dbfs = -240.0
        self.hold_peak_vpp = 0.0
        self.hold_peak_freq_khz = 0.0
        self.audio_phase = 0.0
        self.fm_last_phase = 0.0
        self.audio_sink = None
        self.audio_io = None
        self.audio_ready = False
        self.audio_rate = int(args.audio_rate)
        self.audio_gain = 1.0
        self.last_snr_db = 0.0
        self.last_peak_dbfs = -240.0
        self.audio_gate_open = False

        self.setWindowTitle("HF SDR Receiver")
        self.resize(1050, 760)
        self._build_ui()
        self._init_audio()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_plots)
        self.timer.start(args.update_ms)
        self.audio_timer = QtCore.QTimer(self)
        self.audio_timer.timeout.connect(self.update_audio)
        self.audio_timer.start(20)

    def _make_freqs(self, sample_rate):
        return np.fft.fftshift(np.fft.fftfreq(
            self.fft_size, d=1.0 / sample_rate)) / 1000.0

    def _make_scope_t(self, sample_rate):
        return np.arange(self.scope_size) / sample_rate * 1000.0

    def _update_sample_rate_axes(self, sample_rate):
        if sample_rate <= 0:
            return
        if abs(sample_rate - self.display_sample_rate) < 1.0:
            return

        self.display_sample_rate = sample_rate
        self.freqs = self._make_freqs(sample_rate)
        self.scope_t = self._make_scope_t(sample_rate)
        self.fft_plot.setXRange(-sample_rate / 2000.0, sample_rate / 2000.0,
                                padding=0)

    @staticmethod
    def _dbfs_from_code(value):
        return 20.0 * np.log10(max(float(value), 1e-12) / 32768.0)

    def _code_to_vpp(self, peak_code):
        return float(peak_code) / 32768.0 * self.args.adc_full_scale_vpp

    def _init_audio(self):
        fmt = QtMultimedia.QAudioFormat()
        fmt.setSampleRate(int(self.args.audio_rate))
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QtMultimedia.QAudioFormat.SampleFormat.Int16)

        device = QtMultimedia.QMediaDevices.defaultAudioOutput()
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QtMultimedia.QAudioFormat.SampleFormat.Int16)

        self.audio_rate = int(fmt.sampleRate())
        self.audio_sink = QtMultimedia.QAudioSink(device, fmt, self)
        self.audio_sink.setBufferSize(fmt.sampleRate())
        self.audio_io = self.audio_sink.start()
        self.audio_ready = self.audio_io is not None
        if self.audio_ready:
            self.audio_io.write(np.zeros(1024, dtype="<i2").tobytes())

    def _demodulate_audio(self, samples, sample_rate):
        mode = self.mode_combo.currentText()
        offset_hz = float(self.audio_offset_spin.value())
        bandwidth_hz = float(self.audio_bw_spin.value())
        n = len(samples)
        if n == 0 or sample_rate <= 0:
            return np.empty(0, dtype=np.float32)

        phase_step = 2.0 * np.pi * offset_hz / sample_rate
        phase = self.audio_phase + phase_step * np.arange(n, dtype=np.float32)
        shifted = samples * np.exp(-1j * phase).astype(np.complex64)
        self.audio_phase = float((self.audio_phase + phase_step * n)
                                 % (2.0 * np.pi))

        audio_rate = int(self.audio_rate)
        if mode == "AM":
            demod = np.abs(shifted)
            demod -= np.mean(demod)
            demod = self._fft_filter_real(demod, sample_rate, 80.0,
                                          bandwidth_hz)
        elif mode == "NFM":
            angles = np.angle(shifted)
            unwrapped = np.unwrap(np.concatenate(
                ([self.fm_last_phase], angles.astype(np.float32))))
            self.fm_last_phase = float(angles[-1])
            demod = np.diff(unwrapped)
            demod = self._fft_filter_real(demod, sample_rate, 80.0,
                                          bandwidth_hz)
        else:
            sideband = shifted
            if mode == "USB":
                sideband = self._fft_filter_complex(shifted, sample_rate,
                                                    100.0, bandwidth_hz)
            elif mode == "LSB":
                sideband = self._fft_filter_complex(shifted, sample_rate,
                                                    -bandwidth_hz, -100.0)
            elif mode == "CW":
                sideband = self._fft_filter_complex(shifted, sample_rate,
                                                    -600.0, 600.0)
                tone_step = 2.0 * np.pi * 700.0 / sample_rate
                tone = np.exp(1j * tone_step * np.arange(n,
                                                        dtype=np.float32))
                sideband *= tone.astype(np.complex64)
            demod = np.real(sideband)
            demod -= np.mean(demod)

        return self._resample_audio(demod.astype(np.float32), sample_rate,
                                    audio_rate)

    @staticmethod
    def _fft_filter_complex(samples, sample_rate, low_hz, high_hz):
        freqs = np.fft.fftfreq(len(samples), d=1.0 / sample_rate)
        spec = np.fft.fft(samples)
        mask = (freqs >= low_hz) & (freqs <= high_hz)
        return np.fft.ifft(spec * mask).astype(np.complex64)

    @staticmethod
    def _fft_filter_real(samples, sample_rate, low_hz, high_hz):
        freqs = np.fft.rfftfreq(len(samples), d=1.0 / sample_rate)
        spec = np.fft.rfft(samples)
        mask = (freqs >= low_hz) & (freqs <= high_hz)
        return np.fft.irfft(spec * mask, n=len(samples)).astype(np.float32)

    @staticmethod
    def _resample_audio(samples, sample_rate, audio_rate):
        if len(samples) == 0:
            return samples
        out_len = int(len(samples) * audio_rate / sample_rate)
        if out_len <= 1:
            return np.empty(0, dtype=np.float32)
        src_x = np.arange(len(samples), dtype=np.float32)
        dst_x = np.linspace(0.0, float(len(samples) - 1), out_len,
                            dtype=np.float32)
        return np.interp(dst_x, src_x, samples).astype(np.float32)

    def _build_ui(self):
        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)

        controls = QtWidgets.QHBoxLayout()
        band_controls = QtWidgets.QHBoxLayout()
        audio_controls = QtWidgets.QHBoxLayout()
        self.freq_edit = QtWidgets.QLineEdit("5.0M")
        self.freq_edit.setMaximumWidth(160)
        self.freq_edit.returnPressed.connect(self.tune)
        tune_button = QtWidgets.QPushButton("Tune")
        tune_button.clicked.connect(self.tune)
        self.band_combo = QtWidgets.QComboBox()
        for label, freq in BAND_PRESETS:
            self.band_combo.addItem(f"{label} {freq}", freq)
        self.band_combo.currentIndexChanged.connect(self.select_band)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["AM", "USB", "LSB", "CW", "NFM"])
        self.audio_enable = QtWidgets.QCheckBox("Audio")
        self.audio_offset_spin = QtWidgets.QSpinBox()
        self.audio_offset_spin.setRange(-120_000, 120_000)
        self.audio_offset_spin.setSingleStep(100)
        self.audio_offset_spin.setSuffix(" Hz")
        self.audio_offset_spin.setValue(0)
        self.audio_offset_spin.valueChanged.connect(self.update_audio_marker)
        self.audio_bw_spin = QtWidgets.QSpinBox()
        self.audio_bw_spin.setRange(300, 20_000)
        self.audio_bw_spin.setSingleStep(100)
        self.audio_bw_spin.setSuffix(" Hz")
        self.audio_bw_spin.setValue(3000)
        self.audio_bw_spin.valueChanged.connect(self.update_audio_marker)
        self.volume_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(25)
        self.volume_slider.setMaximumWidth(120)
        self.audio_agc_enable = QtWidgets.QCheckBox("AGC")
        self.audio_agc_enable.setChecked(True)
        self.peak_track_enable = QtWidgets.QCheckBox("Track Peak")
        self.peak_track_enable.setChecked(False)
        self.snr_squelch_spin = QtWidgets.QSpinBox()
        self.snr_squelch_spin.setRange(0, 40)
        self.snr_squelch_spin.setSuffix(" dB SNR")
        self.snr_squelch_spin.setValue(10)
        self.squelch_spin = QtWidgets.QSpinBox()
        self.squelch_spin.setRange(-140, 0)
        self.squelch_spin.setSuffix(" dB")
        self.squelch_spin.setValue(-115)
        self.board_label = QtWidgets.QLabel("Board: ?")
        self.center_label = QtWidgets.QLabel("Center: 5.000000 MHz")
        self.rate_label = QtWidgets.QLabel("Rate: 0 IQ/s")
        self.level_label = QtWidgets.QLabel("Level: --")
        self.status_label = QtWidgets.QLabel("Waiting")

        controls.addWidget(QtWidgets.QLabel("Center"))
        controls.addWidget(self.freq_edit)
        controls.addWidget(tune_button)
        controls.addSpacing(20)
        controls.addWidget(self.board_label)
        controls.addWidget(self.center_label)
        controls.addWidget(self.rate_label)
        controls.addWidget(self.level_label)
        controls.addStretch(1)
        band_controls.addWidget(QtWidgets.QLabel("Band"))
        band_controls.addWidget(self.band_combo)
        band_controls.addWidget(QtWidgets.QLabel("Mode"))
        band_controls.addWidget(self.mode_combo)
        band_controls.addWidget(QtWidgets.QLabel("Offset"))
        band_controls.addWidget(self.audio_offset_spin)
        band_controls.addWidget(QtWidgets.QLabel("BW"))
        band_controls.addWidget(self.audio_bw_spin)
        band_controls.addStretch(1)
        audio_controls.addWidget(self.audio_enable)
        audio_controls.addWidget(QtWidgets.QLabel("Vol"))
        audio_controls.addWidget(self.volume_slider)
        audio_controls.addWidget(self.audio_agc_enable)
        audio_controls.addWidget(self.peak_track_enable)
        audio_controls.addWidget(QtWidgets.QLabel("Min"))
        audio_controls.addWidget(self.snr_squelch_spin)
        audio_controls.addWidget(QtWidgets.QLabel("Squelch"))
        audio_controls.addWidget(self.squelch_spin)
        audio_controls.addStretch(1)
        layout.addLayout(controls)
        layout.addLayout(band_controls)
        layout.addLayout(audio_controls)
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
        self.audio_region = pg.LinearRegionItem(
            values=(-1.5, 1.5), brush=pg.mkBrush(80, 160, 80, 35),
            movable=False)
        self.audio_line = pg.InfiniteLine(
            pos=0.0, angle=90, pen=pg.mkPen("#4caf50", width=1))
        self.fft_plot.addItem(self.audio_region)
        self.fft_plot.addItem(self.audio_line)
        self.fft_plot.scene().sigMouseClicked.connect(self.fft_clicked)
        self.update_audio_marker()

        layout.addWidget(self.scope_plot, 1)
        layout.addWidget(self.fft_plot, 1)
        self.setCentralWidget(root)

    def select_band(self):
        self.freq_edit.setText(self.band_combo.currentData())
        self.tune()

    def fft_clicked(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        pos = self.fft_plot.plotItem.vb.mapSceneToView(event.scenePos())
        offset_hz = int(round(float(pos.x()) * 1000.0))
        self.audio_offset_spin.setValue(max(self.audio_offset_spin.minimum(),
                                            min(self.audio_offset_spin.maximum(),
                                                offset_hz)))

    def update_audio_marker(self):
        offset_khz = float(self.audio_offset_spin.value()) / 1000.0
        half_bw_khz = float(self.audio_bw_spin.value()) / 2000.0
        self.audio_line.setValue(offset_khz)
        self.audio_region.setRegion((offset_khz - half_bw_khz,
                                     offset_khz + half_bw_khz))

    def set_audio_offset_hz(self, offset_hz):
        self.audio_offset_spin.setValue(max(self.audio_offset_spin.minimum(),
                                            min(self.audio_offset_spin.maximum(),
                                                int(round(offset_hz)))))

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
        stream_sample_rate = float(stats["sample_rate"])
        self._update_sample_rate_axes(stream_sample_rate)

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
        noise_floor_db = float(np.median(magnitude))
        snr_db = peak_db - noise_floor_db
        self.last_snr_db = snr_db
        self.last_peak_dbfs = peak_db
        center_hz = float(stats["center_frequency"])
        peak_rf_mhz = (center_hz + peak_freq_khz * 1000.0) / 1_000_000.0

        i_rms = float(np.sqrt(np.mean(scope_i * scope_i)))
        q_rms = float(np.sqrt(np.mean(scope_q * scope_q)))
        complex_rms = float(np.sqrt(np.mean(np.abs(scope) ** 2)))
        i_peak = float(np.max(np.abs(scope_i)))
        q_peak = float(np.max(np.abs(scope_q)))
        code_peak = max(i_peak, q_peak)
        peak_vpp = self._code_to_vpp(2.0 * code_peak)
        complex_rms_dbfs = self._dbfs_from_code(complex_rms)
        i_rms_dbfs = self._dbfs_from_code(i_rms)
        q_rms_dbfs = self._dbfs_from_code(q_rms)

        if peak_db >= self.hold_peak_dbfs or now >= self.hold_until:
            self.hold_peak_dbfs = peak_db
            self.hold_peak_vpp = peak_vpp
            self.hold_peak_freq_khz = peak_freq_khz
            self.hold_until = now + max(0.1, self.args.level_hold)

        if self.peak_track_enable.isChecked():
            current_offset_hz = float(self.audio_offset_spin.value())
            half_bw_hz = max(250.0, float(self.audio_bw_spin.value()) * 0.5)
            if abs(peak_freq_khz * 1000.0 - current_offset_hz) > half_bw_hz:
                self.set_audio_offset_hz(peak_freq_khz * 1000.0)

        self.board_label.setText(f"Board: {stats['board_ip']}")
        self.center_label.setText(f"Center: {center_hz / 1_000_000.0:.6f} MHz")
        self.rate_label.setText(f"Rate: {sample_rate_est:.0f} IQ/s")
        self.level_label.setText(
            f"Level: pk {peak_vpp:.4g} Vpp, rms {complex_rms_dbfs:.1f} dBFS"
        )
        self.status_label.setText(
            f"packets={stats['total_packets']} lost={stats['lost_packets']} "
            f"win_lost={stats['interval_lost']} "
            f"peak={peak_freq_khz:.1f} kHz {peak_db:.1f} dBFS "
            f"RF={peak_rf_mhz:.6f} MHz noise={noise_floor_db:.1f} dBFS "
            f"SNR={snr_db:.1f} dB "
            f"Irms={i_rms:.1f}/{i_rms_dbfs:.1f}dBFS "
            f"Qrms={q_rms:.1f}/{q_rms_dbfs:.1f}dBFS "
            f"code_pk={code_peak:.0f} est={peak_vpp:.4g}Vpp "
            f"gate={'open' if self.audio_gate_open else 'closed'} "
            f"hold={self.hold_peak_freq_khz:.1f}kHz "
            f"{self.hold_peak_dbfs:.1f}dBFS/{self.hold_peak_vpp:.4g}Vpp | "
            f"{stats['status_text']}"
        )

    def update_audio(self):
        if not self.audio_enable.isChecked() or not self.audio_ready:
            return

        free_audio_samples = self.audio_sink.bytesFree() // 2
        if free_audio_samples < int(self.audio_rate * 0.015):
            return

        max_by_output = int(free_audio_samples * self.display_sample_rate
                            / max(1, self.audio_rate))
        max_count = max(512, min(int(self.display_sample_rate * 0.03),
                                 max_by_output))
        data, sample_rate = self.shared.read_audio(max_count)
        if data is None:
            return

        level_dbfs = self._dbfs_from_code(np.sqrt(
            np.mean(np.abs(data.astype(np.complex64)) ** 2)))
        self.audio_gate_open = (
            level_dbfs >= float(self.squelch_spin.value()) and
            self.last_snr_db >= float(self.snr_squelch_spin.value())
        )
        if not self.audio_gate_open:
            audio = np.zeros(max(1, int(len(data) * self.audio_rate
                                       / sample_rate)), dtype=np.float32)
        else:
            audio = self._demodulate_audio(data, sample_rate)

        if len(audio) == 0:
            return

        audio -= float(np.mean(audio))
        rms = float(np.sqrt(np.mean(audio * audio)))
        if self.audio_agc_enable.isChecked() and rms > 1e-9:
            target_gain = min(100.0, max(0.02, 0.14 / rms))
            self.audio_gain = 0.94 * self.audio_gain + 0.06 * target_gain
            audio *= self.audio_gain
        else:
            self.audio_gain = 1.0
        audio *= float(self.volume_slider.value()) / 100.0
        audio = np.clip(audio, -0.95, 0.95)
        pcm = (audio * 32767.0).astype("<i2").tobytes()

        self.audio_io.write(pcm)

    def closeEvent(self, event):
        self.shared.running = False
        if self.audio_sink is not None:
            self.audio_sink.stop()
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
