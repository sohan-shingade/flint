"""Jupiter Perps sidecar process manager.

Manages the lifecycle of the Node.js Jupiter Perps sidecar, including
start, stop, health-checking, and automatic restarts up to a configured limit.
"""
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)
_SIDECAR_DIR = Path(__file__).resolve().parent.parent.parent / "sidecar" / "jupiter-perps"


class JupiterSidecar:
    def __init__(self, port=8401, rpc_url="", wallet_path="", max_restarts=3):
        self.port = port
        self._rpc_url = rpc_url
        self._wallet_path = wallet_path
        self._max_restarts = max_restarts
        self._restart_count = 0
        self._process: Optional[subprocess.Popen] = None
        self._monitor_thread = None
        self._stop_event = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _check_node(self) -> bool:
        return shutil.which("node") is not None

    def _health_check(self) -> bool:
        try:
            resp = httpx.get(f"{self.base_url}/health", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def start(self) -> bool:
        if not self._check_node():
            logger.error("Node.js not found on PATH")
            return False
        if not _SIDECAR_DIR.exists():
            logger.error(f"Sidecar dir not found: {_SIDECAR_DIR}")
            return False
        env = os.environ.copy()
        env["PORT"] = str(self.port)
        env["RPC_URL"] = self._rpc_url
        env["WALLET_PATH"] = self._wallet_path
        try:
            self._process = subprocess.Popen(
                ["node", "dist/index.js"], cwd=str(_SIDECAR_DIR), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(30):
                time.sleep(0.5)
                if self._health_check():
                    self._start_monitor()
                    return True
            self.stop()
            return False
        except Exception as e:
            logger.error(f"Failed to start sidecar: {e}")
            return False

    def stop(self):
        self._stop_event.set()
        if self._process and self._process.poll() is None:
            self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    def _start_monitor(self):
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _monitor_loop(self):
        while not self._stop_event.is_set():
            self._stop_event.wait(10)
            if self._stop_event.is_set():
                break
            if not self.is_running:
                if self._restart_count < self._max_restarts:
                    self._restart_count += 1
                    self.start()
                else:
                    break
