import json
import socketserver
import threading
import time
import unittest

from live_audio import LiveAudioForwarder


class ForwarderTests(unittest.TestCase):
    def test_full_live_queue_never_blocks_and_disable_discards_it(self):
        forwarder = LiveAudioForwarder("127.0.0.1", 1, "pi-cam", "secret", 44100, lambda _: None)
        forwarder.set_enabled(True)
        started = time.monotonic()
        for _ in range(1000):
            forwarder.write(b"\x01\x00\x02\x00" * 8192)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertLessEqual(forwarder.queue.qsize(), 8)
        forwarder.set_enabled(False)
        self.assertTrue(forwarder.queue.empty())
        forwarder.write(b"disabled")
        self.assertTrue(forwarder.queue.empty())

    def test_reenable_never_replays_previous_session_audio(self):
        forwarder = LiveAudioForwarder("127.0.0.1", 1, "pi-cam", "secret", 44100, lambda _: None)
        forwarder.set_enabled(True)
        forwarder.write(b"old session")
        generation = forwarder.generation
        forwarder.set_enabled(False)
        forwarder.set_enabled(True)
        forwarder.write(b"new session")
        new_generation, _, data = forwarder.queue.get_nowait()
        self.assertGreater(new_generation, generation)
        self.assertEqual(data, b"new session")

    def test_tcp_handshake_audio_and_on_demand_disconnect(self):
        ready = threading.Event()
        received = threading.Event()
        disconnected = threading.Event()
        observations = {}
        expected = b"\x01\x00\x02\x00" * 128

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                observations["handshake"] = json.loads(self.rfile.readline())
                self.wfile.write(b"OK\n")
                self.wfile.flush()
                observations["audio"] = self.rfile.read(len(expected))
                received.set()
                self.rfile.read(1)
                disconnected.set()

        with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            forwarder = LiveAudioForwarder(
                "127.0.0.1",
                server.server_address[1],
                "pi-cam",
                "private-token",
                44100,
                lambda status: ready.set() if status == "streaming" else None,
            )
            forwarder.start()
            try:
                forwarder.set_enabled(True)
                self.assertTrue(ready.wait(3))
                forwarder.write(expected)
                self.assertTrue(received.wait(3))
                self.assertEqual(observations["audio"], expected)
                self.assertEqual(observations["handshake"]["source"], "pi-cam")
                self.assertEqual(observations["handshake"]["sample_rate"], 44100)
                forwarder.set_enabled(False)
                self.assertTrue(disconnected.wait(3))
            finally:
                forwarder.stop()
                server.shutdown()


if __name__ == "__main__":
    unittest.main()
