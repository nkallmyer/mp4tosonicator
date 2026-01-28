# app.py
import os
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QDoubleSpinBox, QSpinBox, QGroupBox, QFormLayout,
    QMessageBox, QTextEdit, QFileDialog
)

from controllers import SerialDevice, SonicatorController, discover_devices
from sequencer import AudioRunWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Q125 Audio Sonication Controller")

        self.dev_q125: Optional[SerialDevice] = None
        self.q125: Optional[SonicatorController] = None
        self.worker: Optional[AudioRunWorker] = None
        self.audio_path: Optional[str] = None
        self.soundfont_path: Optional[str] = None

        self._build_ui()

    # ---------------- UI ----------------

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        # Buttons
        top = QHBoxLayout()
        self.btn_discover = QPushButton("Discover Sonicator")
        self.btn_connect = QPushButton("Connect")
        self.btn_load_audio = QPushButton("Load Audio")
        self.btn_start = QPushButton("Start")
        self.btn_pause = QPushButton("Pause")
        self.btn_stop = QPushButton("Stop")
        self.btn_pause.setCheckable(True)

        top.addWidget(self.btn_discover)
        top.addWidget(self.btn_connect)
        top.addWidget(self.btn_load_audio)
        top.addWidget(self.btn_start)
        top.addWidget(self.btn_pause)
        top.addWidget(self.btn_stop)
        layout.addLayout(top)

        self.lbl_status = QLabel("Status: disconnected")
        layout.addWidget(self.lbl_status)

        self.lbl_audio = QLabel("Audio: (none)")
        self.lbl_audio.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.lbl_audio)

        self.lbl_soundfont = QLabel("Soundfont: (none)")
        self.lbl_soundfont.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.lbl_soundfont)

        # Audio settings
        audio_box = QGroupBox("Audio Sonication Settings")
        form = QFormLayout(audio_box)

        self.spin_min_power = QDoubleSpinBox()
        self.spin_min_power.setRange(0, 200)
        self.spin_min_power.setDecimals(2)
        self.spin_min_power.setValue(5.0)

        self.spin_max_power = QDoubleSpinBox()
        self.spin_max_power.setRange(0, 200)
        self.spin_max_power.setDecimals(2)
        self.spin_max_power.setValue(60.0)

        self.spin_frame_ms = QSpinBox()
        self.spin_frame_ms.setRange(5, 500)
        self.spin_frame_ms.setValue(50)

        form.addRow("Min power (W)", self.spin_min_power)
        form.addRow("Max power (W)", self.spin_max_power)
        form.addRow("Frame size (ms)", self.spin_frame_ms)

        self.btn_load_soundfont = QPushButton("Load Soundfont (MIDI)")
        form.addRow(self.btn_load_soundfont)

        layout.addWidget(audio_box)

        # Log
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        # Wire actions
        self.btn_discover.clicked.connect(self.on_discover)
        self.btn_connect.clicked.connect(self.on_connect)
        self.btn_load_audio.clicked.connect(self.on_load_audio)
        self.btn_start.clicked.connect(self.on_start)
        self.btn_pause.clicked.connect(self.on_pause)
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_load_soundfont.clicked.connect(self.on_load_soundfont)

        self._set_buttons_connected(False)

    def _set_buttons_connected(self, connected: bool):
        self.btn_start.setEnabled(connected)
        self.btn_pause.setEnabled(connected)
        self.btn_stop.setEnabled(connected)

    def log_line(self, s: str):
        self.log.append(s)
        self.log.ensureCursorVisible()

    # ---------------- Actions ----------------

    def on_discover(self):
        self._close_devices()
        self.log_line("Discovering sonicator...")
        self.dev_q125 = discover_devices(self.log_line)

        if not self.dev_q125:
            QMessageBox.warning(self, "Discovery", "Could not find the sonicator controller.\n"
                                                 "Ensure the Arduino is plugged in and printing its startup banner.")
            self.lbl_status.setText("Status: discovery failed")
            return

        self.lbl_status.setText(f"Status: discovered Q125={self.dev_q125.port}")

    def on_connect(self):
        if not self.dev_q125:
            QMessageBox.warning(self, "Connect", "Run Discover Sonicator first.")
            return

        self.q125 = SonicatorController(self.dev_q125, self.log_line)

        # Clear any startup chatter
        self.dev_q125.flush_input()

        self._set_buttons_connected(True)
        self.lbl_status.setText("Status: connected")
        self.log_line("Connected. Buffers cleared.")

    def on_load_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select audio file",
            "",
            "Audio Files (*.wav *.mp3 *.mp4 *.m4a *.mid *.midi)"
        )
        if not path:
            return
        self.audio_path = path
        self.lbl_audio.setText(f"Audio: {path}")

    def on_load_soundfont(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select soundfont file",
            "",
            "Soundfont Files (*.sf2)"
        )
        if not path:
            return
        self.soundfont_path = path
        self.lbl_soundfont.setText(f"Soundfont: {path}")

    def _close_devices(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(2000)
        if self.dev_q125:
            self.dev_q125.close()
        self.dev_q125 = None
        self.q125 = None
        self._set_buttons_connected(False)

    def closeEvent(self, event):
        self._close_devices()
        super().closeEvent(event)

    def on_start(self):
        if not self.q125:
            return
        if not self.audio_path:
            QMessageBox.warning(self, "Audio", "Select an audio file first.")
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Audio", "Audio playback is already running.")
            return

        ext = os.path.splitext(self.audio_path)[1].lower()
        if ext in {".mid", ".midi"} and not self.soundfont_path:
            QMessageBox.warning(self, "Audio", "Select a soundfont (.sf2) for MIDI playback.")
            return

        min_power = float(self.spin_min_power.value())
        max_power = float(self.spin_max_power.value())
        if max_power < min_power:
            QMessageBox.warning(self, "Settings", "Max power must be >= min power.")
            return

        frame_ms = int(self.spin_frame_ms.value())

        self.worker = AudioRunWorker(
            self.q125,
            self.audio_path,
            min_power,
            max_power,
            frame_ms,
            soundfont_path=self.soundfont_path,
        )
        self.worker.sig_log.connect(self.log_line)
        self.worker.sig_done.connect(self._on_run_done)

        self.btn_pause.setChecked(False)
        self.worker.start()
        self.lbl_status.setText("Status: sonication running")

    def on_pause(self):
        if not self.worker:
            return
        paused = self.btn_pause.isChecked()
        self.worker.pause(paused)
        self.log_line("PAUSED" if paused else "RESUMED")

    def on_stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.log_line("Stop requested...")

    def _on_run_done(self, ok: bool, msg: str):
        self.lbl_status.setText("Status: idle" if ok else "Status: stopped/error")
        self.log_line(f"=== DONE: {'OK' if ok else 'FAIL'}: {msg} ===")
