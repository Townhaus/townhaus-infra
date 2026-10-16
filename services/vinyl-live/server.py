"""On-demand PCM ingest, MP3 fanout, and MQTT controls. No audio device access."""

import asyncio
import contextlib
import hmac
import json
import logging
import os
import signal
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from aiohttp import web

SOURCES = ("pi-cam", "aswitch")
PREFIX = "vinyl-live"
LOG = logging.getLogger("vinyl_live")


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


class Source:
    def __init__(self, name, publish, max_listeners=20):
        self.name = name
        self.publish = publish
        self.enabled = False
        self.task = None
        self.process = None
        self.listeners = set()
        self.max_listeners = max_listeners
        self.streaming = False

    def status(self, value):
        self.publish(f"{PREFIX}/{self.name}/status", value)

    def broadcast(self, data):
        for listener in list(self.listeners):
            if listener.full():
                self.listeners.discard(listener)
                while not listener.empty():
                    listener.get_nowait()
                listener.put_nowait(None)
            else:
                listener.put_nowait(data)

    async def encode(self):
        self.process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-i",
            "pipe:0",
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            "-f",
            "mp3",
            "-write_xing",
            "0",
            "-id3v2_version",
            "0",
            "-flush_packets",
            "1",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def output(self):
        while data := await self.process.stdout.read(4096):
            if not self.streaming:
                self.streaming = True
                self.status("streaming")
            self.broadcast(data)

    async def finish(self):
        self.streaming = False
        self.broadcast(None)
        if self.process is not None:
            if self.process.stdin is not None:
                self.process.stdin.close()
            if self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 3)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        self.process.kill()
                    await self.process.wait()
            self.process = None

    async def stop(self):
        self.enabled = False
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.status("off")

    async def listen(self, request):
        if not self.enabled or not self.streaming:
            raise web.HTTPServiceUnavailable(text="This source is off or waiting for audio.\n")
        if len(self.listeners) >= self.max_listeners:
            raise web.HTTPServiceUnavailable(text="Listener limit reached.\n")
        listener = asyncio.Queue(maxsize=16)
        self.listeners.add(listener)
        response = web.StreamResponse(
            headers={
                "Content-Type": "audio/mpeg",
                "Cache-Control": "no-store, no-transform",
                "X-Accel-Buffering": "no",
                "icy-name": self.name,
            }
        )
        response.force_close()
        try:
            await response.prepare(request)
            while (data := await listener.get()) is not None:
                await asyncio.wait_for(response.write(data), 5)
        except (ConnectionError, TimeoutError):
            pass
        finally:
            self.listeners.discard(listener)
        return response


class LiveServer:
    def __init__(self, tokens, control_dir, tunnel_available=False):
        self.tokens = tokens
        self.control_dir = Path(control_dir)
        self.control_dir.mkdir(parents=True, exist_ok=True)
        self.tunnel_available = tunnel_available
        self.tunnel_requested = False
        self.tunnel_state = "off"
        self.client = None
        self.events = asyncio.Queue(maxsize=128)
        self.sources = {name: Source(name, self.publish) for name in SOURCES}

    def publish(self, topic, payload, retain=True):
        if self.client is not None:
            return self.client.publish(topic, payload, qos=1, retain=retain)
        return None

    def source_command(self, name):
        self.publish(
            f"{name}/audio_live/set",
            "on" if self.sources[name].enabled else "off",
            False,
        )

    def discover(self):
        device = {
            "identifiers": ["townhaus_vinyl_live"],
            "name": "Vinyl Live",
            "manufacturer": "Townhaus",
        }
        for name, label in [
            ("pi-cam", "Lounge Live Stream"),
            ("aswitch", "Aswitch Live Stream"),
            ("tunnel", "Public Tunnel"),
        ]:
            identity = f"vinyl_live_{name.replace('-', '_')}"
            availability = [{"topic": f"{PREFIX}/availability"}]
            if name == "tunnel":
                availability.append({"topic": f"{PREFIX}/tunnel/availability"})
            else:
                availability.append({"topic": f"{name}/audio_live/availability"})
            config = {
                "name": label,
                "unique_id": identity,
                "device": device,
                "command_topic": f"{PREFIX}/{name}/set",
                "state_topic": f"{PREFIX}/{name}/state",
                "payload_on": "on",
                "payload_off": "off",
                "retain": False,
                "qos": 1,
                "availability": availability,
                "availability_mode": "all",
                "icon": "mdi:broadcast",
            }
            self.publish(f"homeassistant/switch/{identity}/config", json.dumps(config))
            if name != "tunnel":
                self.publish(
                    f"homeassistant/sensor/{identity}_status/config",
                    json.dumps(
                        {
                            "name": f"{label} Status",
                            "unique_id": f"{identity}_status",
                            "device": device,
                            "state_topic": f"{PREFIX}/{name}/status",
                            "availability": availability,
                            "availability_mode": "all",
                            "icon": "mdi:waveform",
                        }
                    ),
                )

    async def set_source(self, name, enabled):
        source = self.sources[name]
        if enabled:
            source.enabled = True
            source.status("streaming" if source.streaming else "waiting")
        else:
            await source.stop()
        self.source_command(name)
        self.publish(f"{PREFIX}/{name}/state", "on" if source.enabled else "off")

    async def ingest(self, reader, writer):
        source = None
        output = None
        try:
            handshake = json.loads(await asyncio.wait_for(reader.readline(), 5))
            name = handshake.get("source")
            token = handshake.get("token")
            if name not in self.sources or not isinstance(token, str):
                raise ValueError("Invalid source")
            if not hmac.compare_digest(token, self.tokens[name]):
                raise ValueError("Invalid credential")
            if (
                handshake.get("sample_rate"),
                handshake.get("channels"),
                handshake.get("format"),
            ) != (44100, 2, "s16le"):
                raise ValueError("Invalid PCM format")
            candidate = self.sources[name]
            if not candidate.enabled or candidate.task is not None:
                raise ValueError("Source is off or already connected")
            source = candidate
            source.task = asyncio.current_task()
            await source.encode()
            output = asyncio.create_task(source.output())
            writer.write(b"OK\n")
            await writer.drain()
            while data := await asyncio.wait_for(reader.read(32768), 5):
                source.process.stdin.write(data)
                await asyncio.wait_for(source.process.stdin.drain(), 2)
        except (
            ValueError,
            KeyError,
            TypeError,
            ConnectionError,
            TimeoutError,
            OSError,
        ):
            LOG.info("Live ingest connection ended or was rejected")
        finally:
            if output is not None:
                output.cancel()
                await asyncio.gather(output, return_exceptions=True)
            if source is not None:
                await source.finish()
                source.task = None
                source.status("waiting" if source.enabled else "off")
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def listener(self, request):
        name = request.match_info["source"]
        if name not in self.sources:
            raise web.HTTPNotFound()
        return await self.sources[name].listen(request)

    async def health(self, request):
        return web.json_response(
            {
                "sources": {
                    name: {"enabled": source.enabled, "streaming": source.streaming}
                    for name, source in self.sources.items()
                },
                "tunnel": self.tunnel_state,
            }
        )

    async def control(self):
        while True:
            action, name, value = await self.events.get()
            if action == "source":
                await self.set_source(name, value)
            elif action == "refresh":
                self.source_command(name)
            elif action == "tunnel":
                if value and not self.tunnel_available:
                    self.publish(f"{PREFIX}/error", "Tunnel token is not configured", False)
                    self.publish(f"{PREFIX}/tunnel/state", "off")
                    continue
                self.tunnel_requested = value
            elif action == "connected":
                self.discover()
                self.publish(
                    f"{PREFIX}/tunnel/availability",
                    "online" if self.tunnel_available else "offline",
                )
                for source_name, source in self.sources.items():
                    self.publish(
                        f"{PREFIX}/{source_name}/state",
                        "on" if source.enabled else "off",
                    )
                    source.status(
                        "streaming" if source.streaming else "waiting" if source.enabled else "off"
                    )
                    self.source_command(source_name)
                self.publish(f"{PREFIX}/tunnel/state", self.tunnel_state)
                self.publish(f"{PREFIX}/availability", "online")
            elif action == "disconnected":
                self.tunnel_requested = False
                for source_name in SOURCES:
                    await self.set_source(source_name, False)

    async def tunnel_lease(self):
        request_path = self.control_dir / "tunnel-request.json"
        status_path = self.control_dir / "tunnel-status.json"
        # A new controller process never restores a previous broadcast.
        request_path.unlink(missing_ok=True)
        while True:
            if self.tunnel_requested:
                now = time.time()
                atomic_json(request_path, {"issued_at": now, "expires_at": now + 5})
            else:
                request_path.unlink(missing_ok=True)
            state = "off"
            try:
                status = json.loads(status_path.read_text())
                if time.time() - status["updated_at"] < 3:
                    state = status["state"]
            except (OSError, ValueError, KeyError, TypeError):
                pass
            if state != self.tunnel_state:
                self.tunnel_state = state
                self.publish(f"{PREFIX}/tunnel/state", state)
            await asyncio.sleep(0.5)

    def connect_mqtt(self, loop, settings=None):
        settings = settings if settings is not None else os.environ
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="vinyl-live-control")
        self.client = client
        if settings.get("MQTT_USER"):
            client.username_pw_set(settings["MQTT_USER"], settings.get("MQTT_PASS"))
        client.will_set(f"{PREFIX}/availability", "offline", qos=1, retain=True)

        def enqueue(event):
            try:
                self.events.put_nowait(event)
            except asyncio.QueueFull:
                LOG.error("Live control queue is full")

        def connected(client, userdata, flags, reason_code, properties):
            if reason_code.is_failure:
                return
            client.subscribe(
                [(f"{PREFIX}/+/set", 1)]
                + [(f"{name}/audio_live/availability", 1) for name in SOURCES]
            )
            loop.call_soon_threadsafe(enqueue, ("connected", None, None))

        def message(client, userdata, message):
            topic = message.topic
            value = message.payload.decode(errors="replace").strip().lower()
            for name in SOURCES:
                if topic == f"{name}/audio_live/availability" and value == "online":
                    loop.call_soon_threadsafe(enqueue, ("refresh", name, None))
                    return
            if message.retain or value not in {"on", "off"}:
                return
            if topic == f"{PREFIX}/tunnel/set":
                event = ("tunnel", None, value == "on")
            elif topic in {f"{PREFIX}/{name}/set" for name in SOURCES}:
                event = ("source", topic.split("/")[1], value == "on")
            else:
                return
            loop.call_soon_threadsafe(enqueue, event)

        def disconnected(client, userdata, flags, reason_code, properties):
            loop.call_soon_threadsafe(enqueue, ("disconnected", None, None))

        client.on_connect = connected
        client.on_message = message
        client.on_disconnect = disconnected
        client.reconnect_delay_set(1, 30)
        client.connect_async(
            settings.get("MQTT_HOST", "homeassistant.local"),
            int(settings.get("MQTT_PORT", "1883")),
            30,
        )
        client.loop_start()

    async def close(self):
        self.tunnel_requested = False
        (self.control_dir / "tunnel-request.json").unlink(missing_ok=True)
        for name in SOURCES:
            await self.set_source(name, False)
        info = self.publish(f"{PREFIX}/availability", "offline")
        if self.client is not None:
            if self.client.is_connected() and info is not None:
                with contextlib.suppress(RuntimeError):
                    await asyncio.to_thread(info.wait_for_publish, timeout=2)
            self.client.disconnect()
            self.client.loop_stop()


async def main():
    tokens = json.loads(
        Path(os.environ.get("SOURCE_TOKENS_FILE", "/run/secrets/source-tokens.json")).read_text()
    )
    if any(not isinstance(tokens.get(name), str) or len(tokens[name]) < 32 for name in SOURCES):
        raise ValueError("Both source credentials must contain at least 32 characters")
    service = LiveServer(
        tokens,
        os.environ.get("CONTROL_DIR", "/control"),
        os.environ.get("TUNNEL_AVAILABLE") == "true",
    )
    app = web.Application()
    app.router.add_get("/{source}.mp3", service.listener, allow_head=False)
    app.router.add_get("/healthz", service.health)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=3)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("LISTEN_PORT", "8081"))).start()
    ingest = await asyncio.start_server(
        service.ingest,
        "0.0.0.0",
        int(os.environ.get("INGEST_PORT", "8080")),
        limit=2048,
    )
    tasks = [
        asyncio.create_task(service.control()),
        asyncio.create_task(service.tunnel_lease()),
    ]
    mqtt_settings = None
    if os.environ.get("MQTT_SETTINGS_FILE"):
        mqtt_settings = json.loads(Path(os.environ["MQTT_SETTINGS_FILE"]).read_text())
    service.connect_mqtt(asyncio.get_running_loop(), mqtt_settings)
    stopped = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, stopped.set)
    try:
        await stopped.wait()
    finally:
        ingest.close()
        await ingest.wait_closed()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.close()
        await runner.cleanup()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(main())
