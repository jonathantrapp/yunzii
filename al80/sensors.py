"""PC readings for the system panel.

Readings come from two places:
  * this process: CPU, RAM and disk through psutil, GPU through nvidia-smi;
  * another program (for example a hardware monitor) that POSTs values to
    /api/sensors. Pushed values win while they are fresh.

Known keys, all numbers: cpu, gpu, ram, vram, disk (percent), cpu_temp,
gpu_temp (degrees C), fps.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from collections import deque

KEYS = ("cpu", "gpu", "ram", "vram", "disk", "cpu_temp", "gpu_temp", "fps")
PUSH_TTL = 10.0       # seconds a pushed value stays valid
GPU_INTERVAL = 2.0    # nvidia-smi is slow to start, so do not run it every tick
HISTORY = 44


class Sensors:
    def __init__(self):
        self._lock = threading.Lock()
        self._pushed: dict[str, tuple[float, float]] = {}
        self._gpu: dict[str, float] = {}
        self._gpu_at = 0.0
        self._smi = shutil.which("nvidia-smi")
        self.history = {"cpu": deque(maxlen=HISTORY), "gpu": deque(maxlen=HISTORY)}
        self._history_at = 0.0
        try:
            import psutil
            self._psutil = psutil
            psutil.cpu_percent(None)   # first call only sets the baseline
        except ImportError:
            self._psutil = None

    def push(self, values: dict) -> list[str]:
        """Accept readings from another program. Returns the keys that were taken."""
        now, taken = time.monotonic(), []
        with self._lock:
            for key, value in values.items():
                if key in KEYS and isinstance(value, (int, float)) and not isinstance(value, bool):
                    self._pushed[key] = (float(value), now)
                    taken.append(key)
        return taken

    def _read_gpu(self):
        if not self._smi or time.monotonic() - self._gpu_at < GPU_INTERVAL:
            return
        self._gpu_at = time.monotonic()
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            out = subprocess.run(
                [self._smi, "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3, creationflags=flags).stdout
            util, temp, used, total = (float(x) for x in out.splitlines()[0].split(","))
            self._gpu = {"gpu": util, "gpu_temp": temp, "vram": 100 * used / total}
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            self._gpu = {}

    def read(self) -> dict[str, float]:
        values: dict[str, float] = {}
        if self._psutil:
            ps = self._psutil
            values["cpu"] = ps.cpu_percent(None)
            values["ram"] = ps.virtual_memory().percent
            try:
                values["disk"] = ps.disk_usage("C:\\" if sys.platform == "win32" else "/").percent
            except OSError:
                pass
        self._read_gpu()
        values.update(self._gpu)
        now = time.monotonic()
        with self._lock:
            for key, (value, at) in self._pushed.items():
                if now - at < PUSH_TTL:
                    values[key] = value
        if now - self._history_at >= 1.0:
            self._history_at = now
            for key, series in self.history.items():
                if key in values:
                    series.append(values[key])
        return values
