"""Run cloudflared only while the live controller renews its short lease."""

import contextlib
import json
import logging
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from server import atomic_json


def lease_valid(path, started_at, now):
    try:
        lease = json.loads(path.read_text())
        return started_at <= lease["issued_at"] <= now < lease["expires_at"] <= now + 6
    except (OSError, ValueError, KeyError, TypeError):
        return False


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def main():
    control = Path(os.environ.get("CONTROL_DIR", "/control"))
    started_at = time.time()
    stopping = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopping.set())
    process = None
    retry_at = 0
    try:
        while not stopping.is_set():
            requested = lease_valid(control / "tunnel-request.json", started_at, time.time())
            if requested and process is None and time.monotonic() >= retry_at:
                process = subprocess.Popen(
                    [
                        "cloudflared",
                        "tunnel",
                        "--no-autoupdate",
                        "--metrics",
                        "127.0.0.1:2000",
                        "run",
                        "--token-file",
                        "/run/secrets/tunnel-token",
                    ]
                )
            if process is not None and (not requested or process.poll() is not None):
                stop_process(process)
                process = None
                retry_at = time.monotonic() + 5 if requested else 0
            state = "off"
            if process is not None:
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:2000/ready", timeout=0.5
                    ) as response:
                        if response.status == 200:
                            state = "on"
                except (OSError, urllib.error.URLError):
                    pass
            atomic_json(
                control / "tunnel-status.json",
                {"state": state, "updated_at": time.time()},
            )
            stopping.wait(0.5)
    finally:
        if process is not None:
            stop_process(process)
        with contextlib.suppress(OSError):
            atomic_json(
                control / "tunnel-status.json",
                {"state": "off", "updated_at": time.time()},
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
