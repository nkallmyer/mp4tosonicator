# sequencer.py
import audioop
import contextlib
import os
import shutil
import subprocess
import tempfile
import time
import wave
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from controllers import SonicatorController


class AudioRunWorker(QThread):
    sig_log = pyqtSignal(str)
    sig_done = pyqtSignal(bool, str)

    def __init__(self, q125: SonicatorController, audio_path: str,
                 min_power_w: float, max_power_w: float,
                 frame_ms: int, soundfont_path: Optional[str] = None,
                 parent=None):
        super().__init__(parent)
        self.q125 = q125
        self.audio_path = audio_path
        self.min_power_w = min_power_w
        self.max_power_w = max_power_w
        self.frame_ms = frame_ms
        self.soundfont_path = soundfont_path

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
            time.sleep(0.01)

    def _convert_midi_to_wav(self, path: str) -> Optional[str]:
        if not self.soundfont_path:
            self.log("Select a soundfont (.sf2) to render MIDI.")
            return None
        if not os.path.exists(self.soundfont_path):
            self.log("Soundfont file does not exist.")
            return None
        if not shutil.which("fluidsynth"):
            self.log("fluidsynth not found. Install fluidsynth to render MIDI.")
            return None

        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        cmd = [
            "fluidsynth",
            "-ni",
            self.soundfont_path,
            path,
            "-F",
            wav_path,
            "-r",
            "44100",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            self.log(f"fluidsynth failed: {result.stderr.strip()}")
            os.remove(wav_path)
            return None
        return wav_path

    def _convert_to_wav(self, path: str) -> Optional[str]:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".wav":
            return path
        if ext in {".mid", ".midi"}:
            return self._convert_midi_to_wav(path)

        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        cmd = ["ffmpeg", "-y", "-i", path, "-ac", "1", "-ar", "44100", wav_path]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            self.log("ffmpeg not found. Install ffmpeg to convert mp3/mp4 files.")
            os.remove(wav_path)
            return None

        if result.returncode != 0:
            self.log(f"ffmpeg failed: {result.stderr.strip()}")
            os.remove(wav_path)
            return None

        return wav_path

    def _power_from_rms(self, rms: float, max_rms: float) -> float:
        if max_rms <= 0:
            return self.min_power_w
        norm = min(max(rms / max_rms, 0.0), 1.0)
        return self.min_power_w + norm * (self.max_power_w - self.min_power_w)

    def run(self):
        wav_path = None
        try:
            wav_path = self._convert_to_wav(self.audio_path)
            if not wav_path:
                self.sig_done.emit(False, "Audio conversion failed")
                return

            with contextlib.closing(wave.open(wav_path, "rb")) as wf:
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                frame_rate = wf.getframerate()
                if channels != 1:
                    self.log(f"Expected mono audio; got {channels} channels. Using first channel.")
                frame_size = max(1, int(frame_rate * (self.frame_ms / 1000.0)))
                max_rms = float(2 ** (8 * sample_width - 1))

                self.log("=== START AUDIO SONICATION ===")
                self.q125.run(True)

                while not self._stop:
                    self._wait_pause()
                    data = wf.readframes(frame_size)
                    if not data:
                        break
                    rms = audioop.rms(data, sample_width)
                    power = self._power_from_rms(rms, max_rms)
                    self.q125.set_power(power)
                    self._sleep(self.frame_ms / 1000.0)

                self.q125.run(False)

            if self._stop:
                self.sig_done.emit(False, "Stopped by user")
            else:
                self.sig_done.emit(True, "Audio playback complete")

        except Exception as exc:
            try:
                self.q125.run(False)
            except Exception:
                pass
            self.sig_done.emit(False, f"Exception: {exc}")
        finally:
            if wav_path and wav_path != self.audio_path:
                try:
                    os.remove(wav_path)
                except Exception:
                    pass
