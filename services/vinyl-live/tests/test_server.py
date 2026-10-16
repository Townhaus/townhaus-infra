import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from server import LiveServer, Source, atomic_json
from tunnel import lease_valid


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_assistant_controls_do_not_retain_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            service = LiveServer({}, directory)
            service.client = Mock()
            service.discover()
            configs = {
                call.args[0]: json.loads(call.args[1])
                for call in service.client.publish.call_args_list
            }
            switches = {topic: config for topic, config in configs.items() if "/switch/" in topic}
            self.assertEqual(len(switches), 3)
            self.assertTrue(all(config["retain"] is False for config in switches.values()))
            tunnel = configs["homeassistant/switch/vinyl_live_tunnel/config"]
            self.assertEqual(tunnel["availability_mode"], "all")
            self.assertEqual(tunnel["availability"][1]["topic"], "vinyl-live/tunnel/availability")

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.service = LiveServer(
            {name: "x" * 40 for name in ("pi-cam", "aswitch")}, self.temporary.name
        )

    async def test_startup_and_retained_commands_never_enable_a_broadcast(self):
        with patch("server.mqtt.Client", return_value=Mock()):
            self.service.connect_mqtt(asyncio.get_running_loop())
        client = self.service.client
        client.on_message(
            client,
            None,
            SimpleNamespace(
                topic="vinyl-live/pi-cam/set",
                payload=b"on",
                retain=True,
            ),
        )
        await asyncio.sleep(0)
        self.assertTrue(self.service.events.empty())
        self.assertFalse(any(source.enabled for source in self.service.sources.values()))
        self.assertFalse(self.service.tunnel_requested)
        client.on_message(
            client,
            None,
            SimpleNamespace(
                topic="vinyl-live/aswitch/set",
                payload=b"on",
                retain=False,
            ),
        )
        await asyncio.sleep(0)
        self.assertEqual(self.service.events.get_nowait(), ("source", "aswitch", True))

    async def test_tunnel_without_token_is_rejected(self):
        self.service.client = Mock()
        worker = asyncio.create_task(self.service.control())
        try:
            await self.service.events.put(("tunnel", None, True))
            await asyncio.sleep(0.01)
            self.assertFalse(self.service.tunnel_requested)
            calls = self.service.client.publish.call_args_list
            self.assertTrue(any(call.args[0] == "vinyl-live/error" for call in calls))
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_tunnel_off_leaves_sources_enabled(self):
        self.service.tunnel_available = True
        await self.service.set_source("pi-cam", True)
        worker = asyncio.create_task(self.service.control())
        try:
            await self.service.events.put(("tunnel", None, True))
            await asyncio.sleep(0.01)
            self.assertTrue(self.service.tunnel_requested)
            await self.service.events.put(("tunnel", None, False))
            await asyncio.sleep(0.01)
            self.assertFalse(self.service.tunnel_requested)
            self.assertTrue(self.service.sources["pi-cam"].enabled)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_slow_listener_is_disconnected_without_blocking_other_listeners(self):
        source = Source("pi-cam", lambda *_: None)
        slow = asyncio.Queue(maxsize=1)
        fast = asyncio.Queue(maxsize=2)
        slow.put_nowait(b"old")
        source.listeners.update((slow, fast))
        source.broadcast(b"fresh")
        self.assertIsNone(slow.get_nowait())
        self.assertNotIn(slow, source.listeners)
        self.assertEqual(fast.get_nowait(), b"fresh")


class TunnelLeaseTests(unittest.TestCase):
    def test_supervisor_only_runs_connector_with_a_fresh_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binaries = root / "bin"
            binaries.mkdir()
            marker = root / "running"
            executable = binaries / "cloudflared"
            executable.write_text(
                f"#!{sys.executable}\n"
                "import os, pathlib, signal, sys\n"
                "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
                "marker = pathlib.Path(os.environ['FAKE_CONNECTOR_MARKER'])\n"
                "class Handler(BaseHTTPRequestHandler):\n"
                " def do_GET(self): self.send_response(200); self.end_headers()\n"
                " def log_message(self, *args): pass\n"
                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
                "marker.touch()\n"
                "try: HTTPServer(('127.0.0.1', 2000), Handler).serve_forever()\n"
                "finally: marker.unlink(missing_ok=True)\n"
            )
            executable.chmod(0o755)
            request = root / "tunnel-request.json"
            atomic_json(request, {"issued_at": time.time() - 1, "expires_at": time.time() + 5})
            environment = dict(os.environ, CONTROL_DIR=str(root), FAKE_CONNECTOR_MARKER=str(marker))
            environment["PATH"] = f"{binaries}:{environment['PATH']}"
            process = subprocess.Popen([sys.executable, "tunnel.py"], env=environment)
            try:
                time.sleep(0.7)
                self.assertFalse(marker.exists(), "A pre-startup lease must not start a tunnel")
                now = time.time()
                atomic_json(request, {"issued_at": now, "expires_at": now + 3})
                deadline = time.monotonic() + 2
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(marker.exists())
                request.unlink()
                deadline = time.monotonic() + 2
                while marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(marker.exists(), "Disabling the tunnel must stop the connector")
            finally:
                process.terminate()
                process.wait(timeout=5)

    def test_old_expired_future_and_invalid_leases_do_not_start_tunnel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            now = time.time()
            for issued, expires in [(now - 10, now + 2), (now, now - 1), (now + 1, now + 3)]:
                atomic_json(path, {"issued_at": issued, "expires_at": expires})
                self.assertFalse(lease_valid(path, now - 1, now))
            path.write_text("broken")
            self.assertFalse(lease_valid(path, now - 1, now))
            atomic_json(path, {"issued_at": now, "expires_at": now + 5})
            self.assertTrue(lease_valid(path, now - 1, now))
            self.assertFalse(lease_valid(path, now - 1, now + 6))


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for live encoding integration")
class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = LiveServer(
            {name: "x" * 40 for name in ("pi-cam", "aswitch")}, self.temporary.name
        )
        self.tcp = await asyncio.start_server(self.service.ingest, "127.0.0.1", 0, limit=2048)
        self.port = self.tcp.sockets[0].getsockname()[1]
        app = web.Application()
        app.router.add_get("/{source}.mp3", self.service.listener)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.writers = []
        self.producers = []

    async def asyncTearDown(self):
        for task in self.producers:
            task.cancel()
        await asyncio.gather(*self.producers, return_exceptions=True)
        for writer in self.writers:
            writer.close()
        await self.service.close()
        self.tcp.close()
        await self.tcp.wait_closed()
        await self.client.close()
        self.temporary.cleanup()

    async def connect(self, name, token=None):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.writers.append(writer)
        writer.write(
            json.dumps(
                {
                    "source": name,
                    "token": token or "x" * 40,
                    "sample_rate": 44100,
                    "channels": 2,
                    "format": "s16le",
                }
            ).encode()
            + b"\n"
        )
        await writer.drain()
        return reader, writer

    async def start_source(self, name):
        await self.service.set_source(name, True)
        reader, writer = await self.connect(name)
        self.assertEqual(await asyncio.wait_for(reader.readline(), 3), b"OK\n")

        async def produce():
            while True:
                writer.write(b"\x00\x10\x00\xf0" * 2048)
                await writer.drain()
                await asyncio.sleep(0.02)

        self.producers.append(asyncio.create_task(produce()))
        for _ in range(200):
            if self.service.sources[name].streaming:
                return
            await asyncio.sleep(0.01)
        self.fail("Encoder did not produce MP3 output")

    async def test_off_and_unauthorized_sources_do_not_launch_encoders(self):
        reader, _ = await self.connect("pi-cam")
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b"")
        await self.service.set_source("pi-cam", True)
        reader, _ = await self.connect("pi-cam", token="wrong")
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b"")
        self.assertIsNone(self.service.sources["pi-cam"].process)
        response = await self.client.get("/pi-cam.mp3")
        self.assertEqual(response.status, 503)

    async def test_two_mp3_streams_and_independent_shutdown(self):
        await self.start_source("pi-cam")
        await self.start_source("aswitch")
        lounge = await self.client.get("/pi-cam.mp3")
        mixer = await self.client.get("/aswitch.mp3")
        self.assertEqual(lounge.headers["Content-Type"], "audio/mpeg")
        self.assertIn("no-store", lounge.headers["Cache-Control"])
        encoded = await asyncio.wait_for(lounge.content.readexactly(4096), 3)
        probe = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-f",
            "mp3",
            "-i",
            "pipe:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels",
            "-of",
            "json",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await probe.communicate(encoded)
        stream = json.loads(output)["streams"][0]
        self.assertEqual(
            (stream["codec_name"], stream["sample_rate"], stream["channels"]), ("mp3", "44100", 2)
        )
        self.assertTrue(await asyncio.wait_for(mixer.content.read(1024), 3))
        await self.service.set_source("pi-cam", False)
        await asyncio.wait_for(lounge.read(), 3)
        self.assertIsNone(self.service.sources["pi-cam"].process)
        self.assertTrue(self.service.sources["aswitch"].streaming)
        self.assertTrue(await asyncio.wait_for(mixer.content.read(1024), 3))
        mixer.close()


if __name__ == "__main__":
    unittest.main()
