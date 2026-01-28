# controllers.py
import os
import threading
import time
from typing import Callable, List, Optional, Tuple

import serial
import serial.tools.list_ports


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
#   Q125 SONICATOR CONTROLLER
# =========================

class SonicatorController:
    def __init__(self, dev: SerialDevice, log_fn: Callable[[str], None]):
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

def discover_devices(log_fn: Callable[[str], None], baud_q125: int = 115200) -> Optional[SerialDevice]:
    """
    Identify the Q125 sonicator controller by its startup messages.
    - Sonicator prints: "Q125 CTRL READY..." (or contains "CTRL READY")
    """
    ports = list(serial.tools.list_ports.comports())
    log_fn(f"Found {len(ports)} serial ports")

    q125_dev: Optional[SerialDevice] = None
    opened: List[SerialDevice] = []

    startup_wait_s = 3.0

    for p in ports:
        port = p.device
        if not os.access(port, os.R_OK | os.W_OK):
            log_fn(f"Skipping {port}: insufficient permissions")
            continue

        dev: Optional[SerialDevice] = None
        try:
            dev = SerialDevice(port, baud=baud_q125, timeout=0.2)

            # Opening a serial port often resets an Uno; give it time to print banner
            time.sleep(startup_wait_s / 2)
            lines = dev.read_existing_lines()
            if not lines:
                time.sleep(startup_wait_s / 2)
                lines = dev.read_existing_lines()
            if lines:
                log_fn(f"[{port}@{baud_q125}] " + " | ".join(lines))

            if any(("Q125" in ln) or ("CTRL READY" in ln) for ln in lines):
                q125_dev = dev
                opened.append(dev)
                log_fn(f"Identified Q125 on {port}@{baud_q125}")
                break

        except PermissionError as e:
            log_fn(f"Skipping {port}: permission denied ({e})")
            break
        except Exception as e:
            log_fn(f"Port open failed {port}@{baud_q125}: {e}")
            continue
        finally:
            if dev and dev is not q125_dev:
                dev.close()

    # Close anything not selected
    for dev in opened:
        if dev is not q125_dev:
            dev.close()

    # Clear buffers so next commands are clean
    if q125_dev:
        q125_dev.flush_input()

    return q125_dev
