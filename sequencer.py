# sequencer.py
import time
from typing import List

from PyQt5.QtCore import QThread, pyqtSignal

from controllers import Calib, SlotPlan, GrblController, SonicatorController


class RunWorker(QThread):
    sig_log = pyqtSignal(str)
    sig_done = pyqtSignal(bool, str)

    def __init__(self, grbl: GrblController, q125: SonicatorController,
                 calib: Calib, plans: List[SlotPlan], rinse_slot_idx: int,
                 parent=None):
        super().__init__(parent)
        self.grbl = grbl
        self.q125 = q125
        self.calib = calib
        self.plans = plans
        self.rinse_slot_idx = rinse_slot_idx

        self._stop = False
        self._pause = False

    def log(self, s: str):
        self.sig_log.emit(s)

    def stop(self):
        self._stop = True

    def pause(self, p: bool):
        self._pause = p

    def _wait_pause(self):
        while self._pause and not self._stop:
            time.sleep(0.05)

    def _sleep(self, sec: float):
        t0 = time.time()
        while time.time() - t0 < sec:
            if self._stop:
                return
            self._wait_pause()
            time.sleep(0.05)

    # ----- Kinematics -----

    def _x_for_slot(self, idx: int) -> float:
        return self.calib.x_zero_offset_mm + self.calib.x_dir * (idx * self.calib.x_mm_per_slot)

    def _y_up(self, clearance_mm: float = 0.0) -> float:
        return self.calib.y_up_position(clearance_mm)

    def _y_down(self) -> float:
        return self.calib.y_down_position()

    # ----- Moves -----

    def _move_to_slot(self, idx: int) -> bool:
        ok = self.grbl.move_abs(x=self._x_for_slot(idx), feed=self.calib.feed_xy)
        self._sleep(self.calib.settle_after_move_s)
        return ok

    def _probe_up(self, clearance_mm: float = 0.0) -> bool:
        ok = self.grbl.move_abs(y=self._y_up(clearance_mm), feed=self.calib.feed_y)
        self._sleep(self.calib.settle_after_move_s)
        return ok

    def _probe_down(self) -> bool:
        ok = self.grbl.move_abs(y=self._y_down(), feed=self.calib.feed_y)
        self._sleep(self.calib.settle_after_move_s)
        return ok

    # ----- Sonication -----

    def _sonicate_power_for(self, watts: float, seconds: int) -> bool:
        self.q125.set_power(watts)
        self.q125.run(True)
        self.log(f"Sonicate: target {watts:.2f} W for {seconds}s")
        self._sleep(seconds)
        self.q125.run(False)
        return not self._stop

    def _safe_stop(self):
        try:
            self.q125.run(False)
        except Exception:
            pass
        try:
            self._probe_up(self.calib.y_home_clearance_mm)
        except Exception:
            pass

    # ----- Main -----

    def run(self):
        try:
            # Validate plan
            sample_indices = [i for i, sp in enumerate(self.plans) if sp.kind == "SAMPLE"]
            rinse_marked = [i for i, sp in enumerate(self.plans) if sp.kind == "RINSE"]

            if len(sample_indices) < 1:
                self.sig_done.emit(False, "No SAMPLE slots configured")
                return
            if len(rinse_marked) != 1:
                self.sig_done.emit(False, "Mark exactly ONE slot as RINSE")
                return
            if rinse_marked[0] != self.rinse_slot_idx:
                self.sig_done.emit(False, "Rinse slot index must match the slot marked RINSE")
                return

            self.log("=== START RUN ===")

            # Initialize GRBL
            self.grbl.wake()
            self.grbl.unlock()
            if not self.grbl.home():
                self.sig_done.emit(False, "GRBL homing failed")
                return
            self.grbl.set_work_origin()

            # Ensure safe starting state
            self.q125.run(False)
            self._probe_up(self.calib.y_home_clearance_mm)

            # Loop samples
            for idx in sample_indices:
                if self._stop:
                    break
                self._wait_pause()

                sp = self.plans[idx]
                self.log(f"--- SAMPLE slot {idx}: {sp.power_w:.2f} W for {sp.time_s}s ---")

                if not self._move_to_slot(idx):
                    self._safe_stop()
                    self.sig_done.emit(False, f"Move to sample slot {idx} failed")
                    return
                if not self._probe_down():
                    self._safe_stop()
                    self.sig_done.emit(False, f"Probe down failed at slot {idx}")
                    return
                if not self._sonicate_power_for(sp.power_w, sp.time_s):
                    break
                if not self._probe_up():
                    self._safe_stop()
                    self.sig_done.emit(False, f"Probe up failed after sample slot {idx}")
                    return

                # Rinse between samples
                if self._stop:
                    break

                self.log(f"--- RINSE slot {self.rinse_slot_idx}: {self.calib.rinse_power_w:.2f} W for {self.calib.rinse_time_s}s ---")
                if not self._move_to_slot(self.rinse_slot_idx):
                    self._safe_stop()
                    self.sig_done.emit(False, "Move to rinse slot failed")
                    return
                if not self._probe_down():
                    self._safe_stop()
                    self.sig_done.emit(False, "Probe down failed at rinse slot")
                    return
                if not self._sonicate_power_for(self.calib.rinse_power_w, self.calib.rinse_time_s):
                    break
                if not self._probe_up():
                    self._safe_stop()
                    self.sig_done.emit(False, "Probe up failed after rinse")
                    return

            # End
            self._safe_stop()
            if self._stop:
                self.sig_done.emit(False, "Stopped by user")
            else:
                self.sig_done.emit(True, "Run complete")

        except Exception as e:
            self._safe_stop()
            self.sig_done.emit(False, f"Exception: {e}")
