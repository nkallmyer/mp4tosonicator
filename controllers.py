# controllers.py
import time
import threading
import os
from dataclasses import dataclass
from typing import Optional, List, Tuple

import serial
import serial.tools.list_ports


# =========================
#   CONFIG / CALIBRATION
# =========================

@dataclass
class Calib:
    # Carousel
    slots: int = 12
    x_mm_per_slot: float = 0.5275
    x_dir: int = +1
    x_zero_offset_mm: float = 0.0

    # Probe motion
    y_up_mm: float = 0.0
    y_down_mm: float = -25.0     # delta relative to y_up; usually negative means down in work coords
    y_dir: int = +1
    y_home_clearance_mm: float = 100.0

    # Speeds (mm/min)
    feed_xy: float = 2000.0
    feed_y: float = 800.0

    # Sonication rinse step
    rinse_power_w: float = 20.0
    rinse_time_s: int = 120

    # Dwell times
    settle_after_move_s: float = 0.2

    def y_down_position(self) -> float:
        # Reverse the down direction to match physical motion.
        return self.y_up_mm - self.y_dir * self.y_down_mm

    def y_up_position(self, clearance_mm: float = 0.0) -> float:
        if clearance_mm == 0:
            return self.y_up_mm
        down_delta = self.y_down_position() - self.y_up_mm
        if abs(down_delta) < 1e-9:
            return self.y_up_mm
        up_dir = -1 if down_delta > 0 else 1
        return self.y_up_mm + up_dir * abs(clearance_mm)


@dataclass
class SlotPlan:
    kind: str = "EMPTY"   # EMPTY / SAMPLE / RINSE
    power_w: float = 20.0
    time_s: int = 60


# =========================
#   SERIAL DEVICE WRAPPER
# =========================

class SerialDevice:
    """Thread-safe line-based serial wrapper."""
    def __init__(self, port: str, baud: int, timeout: float = 0.2):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser = serial.Serial(port, baudrate=baud, timeout=timeout)
        self.lock = threading.Lock()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def write_line(self, line: str):
        if not line.endswith("\n"):
            line += "\n"
        with self.lock:
            self.ser.write(line.encode("utf-8", errors="ignore"))

    def read_existing_lines(self) -> List[str]:
        """Read all currently buffered complete lines without blocking too long."""
        lines: List[str] = []
        t_end = time.time() + 0.15
        while time.time() < t_end:
            with self.lock:
                raw = self.ser.readline()
            if not raw:
                break
            s = raw.decode("utf-8", errors="ignore").strip()
            if s:
                lines.append(s)
        return lines

    def flush_input(self):
        with self.lock:
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass


# =========================
#   GRBL CONTROLLER
# =========================

class GrblController:
    def __init__(self, dev: SerialDevice, log_fn):
        self.dev = dev
        self.log = log_fn

    def _drain(self):
        for ln in self.dev.read_existing_lines():
            self.log(f"[GRBL] {ln}")

    def send_and_wait_ok(self, gline: str, timeout_s: float = 3.0) -> Tuple[bool, str]:
        self.dev.write_line(gline)
        t0 = time.time()
        last = ""
        while time.time() - t0 < timeout_s:
            lines = self.dev.read_existing_lines()
            for ln in lines:
                self.log(f"[GRBL] {ln}")
                last = ln
                if ln.lower() == "ok":
                    return True, ln
                if ln.lower().startswith("error") or ln.lower().startswith("alarm"):
                    return False, ln
            time.sleep(0.02)
        return False, last or "timeout"

    def wake(self):
        self.dev.write_line("\r\n\r\n")
        time.sleep(0.3)
        self._drain()

    def unlock(self):
        self.send_and_wait_ok("$X", timeout_s=2.0)

    def home(self) -> bool:
        ok, last = self.send_and_wait_ok("$H", timeout_s=45.0)
        if not ok:
            self.log(f"[GRBL] Homing failed: {last}")
        return ok

    def set_work_origin(self) -> bool:
        ok, last = self.send_and_wait_ok("G10 L20 P1 X0 Y0 Z0", timeout_s=2.0)
        if not ok:
            self.log(f"[GRBL] Set WCS failed: {last}")
        return ok

    def move_abs(self, x: Optional[float] = None, y: Optional[float] = None, feed: Optional[float] = None) -> bool:
        parts = ["G90", "G0"]
        if x is not None:
            parts.append(f"X{x:.3f}")
        if y is not None:
            parts.append(f"Y{y:.3f}")
        if feed is not None:
            parts.append(f"F{feed:.1f}")
        ok, _ = self.send_and_wait_ok(" ".join(parts), timeout_s=10.0)
        return ok

    def move_rel(self, dx: float = 0.0, dy: float = 0.0, feed: Optional[float] = None) -> bool:
        parts = ["G91", "G0"]
        if abs(dx) > 1e-9:
            parts.append(f"X{dx:.3f}")
        if abs(dy) > 1e-9:
            parts.append(f"Y{dy:.3f}")
        if feed is not None:
            parts.append(f"F{feed:.1f}")
        ok, _ = self.send_and_wait_ok(" ".join(parts), timeout_s=10.0)
        self.send_and_wait_ok("G90", timeout_s=2.0)
        return ok


# =========================
#   Q125 SONICATOR CONTROLLER
# =========================

class SonicatorController:
    def __init__(self, dev: SerialDevice, log_fn):
        self.dev = dev
        self.log = log_fn

    def send(self, line: str):
        self.dev.write_line(line)

    def command_expect_prefix(self, cmd: str, prefix: str, timeout_s: float = 2.0) -> Tuple[bool, str]:
        self.send(cmd)
        t0 = time.time()
        last = ""
        while time.time() - t0 < timeout_s:
            for ln in self.dev.read_existing_lines():
                self.log(f"[Q125] {ln}")
                last = ln
                if ln.startswith(prefix):
                    return True, ln
                if ln.startswith("ERR"):
                    return False, ln
            time.sleep(0.02)
        return False, last or "timeout"

    def run(self, on: bool):
        self.send(f"RUN {1 if on else 0}")

    def set_power(self, watts: float):
        self.send(f"SETP {watts:.2f}")

    def read_power(self) -> float:
        ok, ln = self.command_expect_prefix("READP", "WATTS", timeout_s=1.5)
        if not ok:
            return float("nan")
        try:
            return float(ln.split()[1])
        except Exception:
            return float("nan")

    def status(self):
        self.send("STATUS")


# =========================
#   DEVICE DISCOVERY
# =========================

def discover_devices(log_fn, baud_grbl=115200, baud_q125=115200):
    """
    Identify two Arduino UNOs by their startup messages.
    - GRBL typically prints: "Grbl x.x..."
    - Sonicator prints: "Q125 CTRL READY..." (or contains "CTRL READY")
    """
    ports = list(serial.tools.list_ports.comports())
    log_fn(f"Found {len(ports)} serial ports")

    grbl_dev: Optional[SerialDevice] = None
    q125_dev: Optional[SerialDevice] = None
    opened: List[SerialDevice] = []

    startup_wait_s = 3.0

    def _probe_grbl(dev: SerialDevice) -> List[str]:
        """Actively probe for GRBL by requesting status."""
        dev.write_line("\r\n")
        time.sleep(0.2)
        dev.write_line("?")
        time.sleep(0.2)
        lines = dev.read_existing_lines()
        if lines:
            log_fn(f"[{dev.port}@{dev.baud}] " + " | ".join(lines))
        return lines

    for p in ports:
        port = p.device
        permission_denied = False
        if not os.access(port, os.R_OK | os.W_OK):
            log_fn(f"Skipping {port}: insufficient permissions")
            continue

        # Try each port at both bauds
        for baud in (baud_grbl, baud_q125):
            dev: Optional[SerialDevice] = None
            try:
                dev = SerialDevice(port, baud=baud, timeout=0.2)

                # Opening a serial port often resets an Uno; give it time to print banner
                time.sleep(startup_wait_s / 2)
                lines = dev.read_existing_lines()
                if not lines:
                    time.sleep(startup_wait_s / 2)
                    lines = dev.read_existing_lines()
                if lines:
                    log_fn(f"[{port}@{baud}] " + " | ".join(lines))

                if any("Grbl" in ln for ln in lines):
                    grbl_dev = dev
                    opened.append(dev)
                    log_fn(f"Identified GRBL on {port}@{baud}")
                    break

                if any(("Q125" in ln) or ("CTRL READY" in ln) for ln in lines):
                    q125_dev = dev
                    opened.append(dev)
                    log_fn(f"Identified Q125 on {port}@{baud}")
                    break

                probed_lines = _probe_grbl(dev)
                if any("Grbl" in ln for ln in probed_lines) or any(
                    ln.startswith("<") and ln.endswith(">") for ln in probed_lines
                ):
                    grbl_dev = dev
                    opened.append(dev)
                    log_fn(f"Identified GRBL (probe) on {port}@{baud}")
                    break

            except PermissionError as e:
                log_fn(f"Skipping {port}: permission denied ({e})")
                permission_denied = True
                break
            except Exception as e:
                log_fn(f"Port open failed {port}@{baud}: {e}")
                continue
            finally:
                if dev and dev not in (grbl_dev, q125_dev):
                    dev.close()

        if permission_denied:
            continue

        if grbl_dev and q125_dev:
            break

    # Close anything not selected
    for dev in opened:
        if dev not in (grbl_dev, q125_dev):
            dev.close()

    # Clear buffers so next commands are clean (your requirement)
    if grbl_dev:
        grbl_dev.flush_input()
    if q125_dev:
        q125_dev.flush_input()

    return grbl_dev, q125_dev
