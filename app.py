# app.py
from typing import Optional, List

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QComboBox, QTableWidget, QTableWidgetItem, QDoubleSpinBox, QSpinBox,
    QGroupBox, QFormLayout, QMessageBox, QTextEdit
)

from controllers import (
    Calib, SlotPlan,
    SerialDevice, GrblController, SonicatorController,
    discover_devices
)
from sequencer import RunWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Q125 Carousel Sonication Controller")

        self.calib = Calib()
        self.plans = [SlotPlan() for _ in range(self.calib.slots)]

        self.dev_grbl: Optional[SerialDevice] = None
        self.dev_q125: Optional[SerialDevice] = None
        self.grbl: Optional[GrblController] = None
        self.q125: Optional[SonicatorController] = None
        self.worker: Optional[RunWorker] = None

        self._build_ui()

    # ---------------- UI ----------------

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        # Buttons
        top = QHBoxLayout()
        self.btn_discover = QPushButton("Discover Devices")
        self.btn_connect = QPushButton("Connect")
        self.btn_home = QPushButton("Home GRBL")
        self.btn_start = QPushButton("Start Run")
        self.btn_pause = QPushButton("Pause")
        self.btn_stop = QPushButton("Stop")
        self.btn_pause.setCheckable(True)

        top.addWidget(self.btn_discover)
        top.addWidget(self.btn_connect)
        top.addWidget(self.btn_home)
        top.addWidget(self.btn_start)
        top.addWidget(self.btn_pause)
        top.addWidget(self.btn_stop)
        layout.addLayout(top)

        self.lbl_status = QLabel("Status: disconnected")
        layout.addWidget(self.lbl_status)

        # Plan table
        plan_box = QGroupBox("12-slot Plan")
        plan_layout = QVBoxLayout(plan_box)

        self.table = QTableWidget(self.calib.slots, 4)
        self.table.setHorizontalHeaderLabels(["Slot", "Type", "Power (W)", "Time (s)"])
        self.table.verticalHeader().setVisible(False)
        self._init_table()
        plan_layout.addWidget(self.table)

        # Rinse controls
        rinse_row = QHBoxLayout()
        rinse_row.addWidget(QLabel("Rinse slot index:"))
        self.spin_rinse_slot = QSpinBox()
        self.spin_rinse_slot.setRange(0, self.calib.slots - 1)
        self.spin_rinse_slot.setValue(0)
        rinse_row.addWidget(self.spin_rinse_slot)

        rinse_row.addWidget(QLabel("Rinse power (W):"))
        self.spin_rinse_power = QDoubleSpinBox()
        self.spin_rinse_power.setRange(0, 200)
        self.spin_rinse_power.setDecimals(2)
        self.spin_rinse_power.setValue(self.calib.rinse_power_w)
        rinse_row.addWidget(self.spin_rinse_power)

        rinse_row.addWidget(QLabel("Rinse time (s):"))
        self.spin_rinse_time = QSpinBox()
        self.spin_rinse_time.setRange(0, 36000)
        self.spin_rinse_time.setValue(self.calib.rinse_time_s)
        rinse_row.addWidget(self.spin_rinse_time)

        plan_layout.addLayout(rinse_row)
        layout.addWidget(plan_box)

        # Calibration box
        calib_box = QGroupBox("Calibration / Motion Settings")
        form = QFormLayout(calib_box)

        self.spin_x_mm_per_slot = QDoubleSpinBox(); self.spin_x_mm_per_slot.setRange(0.001, 500); self.spin_x_mm_per_slot.setDecimals(4); self.spin_x_mm_per_slot.setValue(self.calib.x_mm_per_slot)
        self.spin_x_zero = QDoubleSpinBox(); self.spin_x_zero.setRange(-10000, 10000); self.spin_x_zero.setDecimals(3); self.spin_x_zero.setValue(self.calib.x_zero_offset_mm)
        self.combo_x_dir = QComboBox(); self.combo_x_dir.addItems(["+1", "-1"]); self.combo_x_dir.setCurrentIndex(0 if self.calib.x_dir == 1 else 1)

        self.spin_y_up = QDoubleSpinBox(); self.spin_y_up.setRange(-10000, 10000); self.spin_y_up.setDecimals(3); self.spin_y_up.setValue(self.calib.y_up_mm)
        self.spin_y_down = QDoubleSpinBox(); self.spin_y_down.setRange(-10000, 10000); self.spin_y_down.setDecimals(3); self.spin_y_down.setValue(self.calib.y_down_mm)
        self.combo_y_dir = QComboBox(); self.combo_y_dir.addItems(["+1", "-1"]); self.combo_y_dir.setCurrentIndex(0 if self.calib.y_dir == 1 else 1)
        self.spin_y_home_clear = QDoubleSpinBox(); self.spin_y_home_clear.setRange(0, 10000); self.spin_y_home_clear.setDecimals(3); self.spin_y_home_clear.setValue(self.calib.y_home_clearance_mm)

        self.spin_feed_xy = QDoubleSpinBox(); self.spin_feed_xy.setRange(10, 20000); self.spin_feed_xy.setDecimals(1); self.spin_feed_xy.setValue(self.calib.feed_xy)
        self.spin_feed_y = QDoubleSpinBox(); self.spin_feed_y.setRange(10, 20000); self.spin_feed_y.setDecimals(1); self.spin_feed_y.setValue(self.calib.feed_y)

        self.spin_settle = QDoubleSpinBox(); self.spin_settle.setRange(0, 10); self.spin_settle.setDecimals(2); self.spin_settle.setValue(self.calib.settle_after_move_s)

        form.addRow("X mm per slot", self.spin_x_mm_per_slot)
        form.addRow("X zero offset (mm)", self.spin_x_zero)
        form.addRow("X direction", self.combo_x_dir)
        form.addRow("Y up position (mm)", self.spin_y_up)
        form.addRow("Y down delta (mm)", self.spin_y_down)
        form.addRow("Y direction", self.combo_y_dir)
        form.addRow("Home retreat (mm)", self.spin_y_home_clear)
        form.addRow("Feed XY (mm/min)", self.spin_feed_xy)
        form.addRow("Feed Y (mm/min)", self.spin_feed_y)
        form.addRow("Settle after moves (s)", self.spin_settle)

        layout.addWidget(calib_box)

        # Log
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        # Wire actions
        self.btn_discover.clicked.connect(self.on_discover)
        self.btn_connect.clicked.connect(self.on_connect)
        self.btn_home.clicked.connect(self.on_home)
        self.btn_start.clicked.connect(self.on_start)
        self.btn_pause.clicked.connect(self.on_pause)
        self.btn_stop.clicked.connect(self.on_stop)

        self._set_buttons_connected(False)

    def _init_table(self):
        for r in range(self.calib.slots):
            item0 = QTableWidgetItem(str(r))
            item0.setFlags(item0.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 0, item0)

            combo = QComboBox()
            combo.addItems(["EMPTY", "SAMPLE", "RINSE"])
            combo.setCurrentText("EMPTY")
            self.table.setCellWidget(r, 1, combo)

            p = QDoubleSpinBox()
            p.setRange(0, 200)
            p.setDecimals(2)
            p.setValue(20.0)
            self.table.setCellWidget(r, 2, p)

            t = QSpinBox()
            t.setRange(0, 36000)
            t.setValue(60)
            self.table.setCellWidget(r, 3, t)

    def _set_buttons_connected(self, connected: bool):
        self.btn_home.setEnabled(connected)
        self.btn_start.setEnabled(connected)
        self.btn_pause.setEnabled(connected)
        self.btn_stop.setEnabled(connected)

    def log_line(self, s: str):
        self.log.append(s)
        self.log.ensureCursorVisible()

    def _read_calib_from_ui(self):
        self.calib.x_mm_per_slot = float(self.spin_x_mm_per_slot.value())
        self.calib.x_zero_offset_mm = float(self.spin_x_zero.value())
        self.calib.x_dir = +1 if self.combo_x_dir.currentText() == "+1" else -1

        self.calib.y_up_mm = float(self.spin_y_up.value())
        self.calib.y_down_mm = float(self.spin_y_down.value())
        self.calib.y_dir = +1 if self.combo_y_dir.currentText() == "+1" else -1
        self.calib.y_home_clearance_mm = float(self.spin_y_home_clear.value())

        self.calib.feed_xy = float(self.spin_feed_xy.value())
        self.calib.feed_y = float(self.spin_feed_y.value())
        self.calib.settle_after_move_s = float(self.spin_settle.value())

        self.calib.rinse_power_w = float(self.spin_rinse_power.value())
        self.calib.rinse_time_s = int(self.spin_rinse_time.value())

    def _read_plan_from_table(self) -> List[SlotPlan]:
        plans: List[SlotPlan] = []
        for r in range(self.calib.slots):
            kind = self.table.cellWidget(r, 1).currentText()
            p = float(self.table.cellWidget(r, 2).value())
            t = int(self.table.cellWidget(r, 3).value())
            plans.append(SlotPlan(kind=kind, power_w=p, time_s=t))
        return plans

    # ---------------- Actions ----------------

    def on_discover(self):
        self._close_devices()
        self.log_line("Discovering devices...")
        def _log(s): self.log_line(s)

        grbl_dev, q125_dev = discover_devices(_log)
        self.dev_grbl, self.dev_q125 = grbl_dev, q125_dev

        if not grbl_dev or not q125_dev:
            QMessageBox.warning(self, "Discovery", "Could not find both devices.\n"
                                                 "Ensure both Arduinos are plugged in and printing startup banners.")
            self.lbl_status.setText("Status: discovery incomplete")
            return

        self.lbl_status.setText(f"Status: discovered GRBL={grbl_dev.port}, Q125={q125_dev.port}")

    def on_connect(self):
        if not self.dev_grbl or not self.dev_q125:
            QMessageBox.warning(self, "Connect", "Run Discover Devices first.")
            return

        self.grbl = GrblController(self.dev_grbl, self.log_line)
        self.q125 = SonicatorController(self.dev_q125, self.log_line)

        # Clear any startup chatter (requirement)
        self.dev_grbl.flush_input()
        self.dev_q125.flush_input()

        self._set_buttons_connected(True)
        self.lbl_status.setText("Status: connected")
        self.log_line("Connected. Buffers cleared.")

    def _close_devices(self):
        for dev in (self.dev_grbl, self.dev_q125):
            if dev:
                dev.close()
        self.dev_grbl = None
        self.dev_q125 = None
        self.grbl = None
        self.q125 = None
        self._set_buttons_connected(False)

    def closeEvent(self, event):
        self._close_devices()
        super().closeEvent(event)

    def on_home(self):
        if not self.grbl:
            return
        self._read_calib_from_ui()
        self.log_line("Homing GRBL...")
        self.grbl.wake()
        self.grbl.unlock()
        ok = self.grbl.home()
        if ok:
            self.grbl.set_work_origin()
            self.log_line("Home OK. Work origin set.")
            ok_move = self.grbl.move_abs(
                y=self.calib.y_up_position(self.calib.y_home_clearance_mm),
                feed=self.calib.feed_y
            )
            if not ok_move:
                self.log_line("Warning: failed to retreat after homing.")
        else:
            self.log_line("Home FAILED.")

    def on_start(self):
        if not (self.grbl and self.q125):
            return

        self._read_calib_from_ui()
        plans = self._read_plan_from_table()
        rinse_idx = int(self.spin_rinse_slot.value())

        rinse_marked = [i for i, sp in enumerate(plans) if sp.kind == "RINSE"]
        if len(rinse_marked) != 1:
            QMessageBox.warning(self, "Plan error", "Mark exactly ONE slot as RINSE in the table.")
            return
        if rinse_marked[0] != rinse_idx:
            QMessageBox.warning(self, "Plan error", "Rinse slot index must match the slot marked RINSE.")
            return

        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Run", "A run is already in progress.")
            return

        self.worker = RunWorker(self.grbl, self.q125, self.calib, plans, rinse_idx)
        self.worker.sig_log.connect(self.log_line)
        self.worker.sig_done.connect(self._on_run_done)

        self.btn_pause.setChecked(False)
        self.worker.start()
        self.lbl_status.setText("Status: running")

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
