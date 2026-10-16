"""Forward selected PCM to Beelink without blocking the capture thread."""

import json
import logging
import queue
import socket
import threading
import time


class LiveAudioForwarder(threading.Thread):
    def __init__(self, host, port, source, token, sample_rate, publish_status):
        super().__init__(name="live-audio", daemon=True)
        self.address = (host, port)
        self.handshake = (
            json.dumps(
                {
                    "source": source,
                    "token": token,
                    "sample_rate": sample_rate,
                    "channels": 2,
                    "format": "s16le",
                }
            ).encode()
            + b"\n"
        )
        self.publish_status = publish_status
        self.logger = logging.getLogger("live_audio")
        self.queue = queue.Queue(maxsize=8)
        self.enabled = threading.Event()
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.connection = None
        self.generation = 0

    def _clear_queue(self):
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return

    def set_enabled(self, enabled):
        with self.lock:
            if self.enabled.is_set() == enabled:
                return
            self.generation += 1
            if enabled:
                self.enabled.set()
            else:
                self.enabled.clear()
            connection = self.connection
        self._clear_queue()
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.publish_status("connecting" if enabled else "off")

    def write(self, pcm_bytes):
        if not self.enabled.is_set():
            return
        with self.lock:
            item = (self.generation, time.monotonic(), pcm_bytes)
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            # Never back up detection or recording for a slow live connection.
            self._clear_queue()

    def stop(self):
        self.stopping.set()
        self.set_enabled(False)
        self.join(timeout=5)

    def run(self):
        self.publish_status("off")
        while not self.stopping.is_set():
            if not self.enabled.wait(0.2):
                continue
            with self.lock:
                generation = self.generation
            try:
                with socket.create_connection(self.address, timeout=2) as connection:
                    connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
                    with self.lock:
                        self.connection = connection
                    connection.sendall(self.handshake)
                    with connection.makefile("rb") as response:
                        if response.readline(32) != b"OK\n":
                            raise ConnectionError("Receiver is not accepting this source")
                    with self.lock:
                        if not self.enabled.is_set() or generation != self.generation:
                            continue
                    self._clear_queue()
                    self.publish_status("streaming")
                    while self.enabled.is_set() and not self.stopping.is_set():
                        with self.lock:
                            if generation != self.generation:
                                break
                        try:
                            block_generation, captured_at, pcm_bytes = self.queue.get(timeout=0.2)
                        except queue.Empty:
                            continue
                        if block_generation == generation and time.monotonic() - captured_at < 0.75:
                            connection.sendall(pcm_bytes)
            except OSError:
                if self.enabled.is_set() and not self.stopping.is_set():
                    self.logger.warning("Live receiver unavailable; retrying with fresh audio")
                    self.publish_status("connecting")
                    self.stopping.wait(1)
            finally:
                with self.lock:
                    self.connection = None
                self._clear_queue()
