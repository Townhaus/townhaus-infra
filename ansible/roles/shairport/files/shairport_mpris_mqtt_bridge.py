#!/usr/bin/python3
"""Publish Shairport Sync MPRIS track metadata using its legacy MQTT topics."""

import asyncio
import base64
import logging
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import paho.mqtt.client as mqtt
from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType


LOG = logging.getLogger("shairport-mpris-mqtt")
SERVICE = "org.mpris.MediaPlayer2.ShairportSync"
PLAYER_PATH = "/org/mpris/MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
COVER_DIRECTORY = Path("/tmp/shairport-sync/.cache/coverart").resolve()


def unwrap(value):
    if isinstance(value, Variant):
        return unwrap(value.value)
    if isinstance(value, dict):
        return {key: unwrap(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [unwrap(item) for item in value]
    return value


def text_value(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def artwork_path(art_url):
    parsed = urlparse(art_url or "")
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    path = Path(unquote(parsed.path)).resolve()
    if not path.is_relative_to(COVER_DIRECTORY):
        LOG.warning("Ignoring artwork outside Shairport's cover cache")
        return None
    return path


class Bridge:
    def __init__(self):
        self.topic_prefix = os.environ["MQTT_TOPIC_PREFIX"].rstrip("/")
        self.latest = None
        self.latest_cover = None
        client_id = os.environ.get("MQTT_CLIENT_ID", "shairport-mpris-bridge")
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
        self.client.username_pw_set(
            base64.b64decode(os.environ["MQTT_USERNAME_B64"]).decode(),
            base64.b64decode(os.environ["MQTT_PASSWORD_B64"]).decode(),
        )
        self.client.on_connect = self.on_connect
        self.client.connect(os.environ["MQTT_HOST"], int(os.environ["MQTT_PORT"]), 60)
        self.client.loop_start()

    def on_connect(self, client, _userdata, _flags, reason_code, _properties):
        if reason_code == 0 and self.latest is not None:
            self.publish_snapshot(self.latest, self.latest_cover)

    def publish(self, suffix, payload):
        result = self.client.publish(f"{self.topic_prefix}/{suffix}", payload, qos=0, retain=False)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            LOG.warning("MQTT publish failed for %s: %s", suffix, result.rc)

    def publish_snapshot(self, metadata, cover):
        for key in ("title", "artist", "album", "genre"):
            self.publish(key, text_value(metadata.get(key)).encode("utf-8"))
        if cover is not None:
            self.publish("cover", cover)

    async def update(self, raw_metadata):
        metadata = unwrap(raw_metadata)
        normalized = {
            "title": metadata.get("xesam:title"),
            "artist": metadata.get("xesam:artist"),
            "album": metadata.get("xesam:album"),
            "genre": metadata.get("xesam:genre"),
        }
        art_url = text_value(metadata.get("mpris:artUrl"))
        art_path = artwork_path(art_url)
        cover = None
        if art_path is not None:
            for _attempt in range(5):
                try:
                    cover = art_path.read_bytes()
                    break
                except FileNotFoundError:
                    await asyncio.sleep(0.2)
            if cover is None:
                LOG.warning("Artwork file is not available yet")

        fingerprint = (metadata.get("mpris:trackid"), *normalized.values(), art_url)
        if fingerprint == getattr(self, "fingerprint", None):
            return
        self.fingerprint = fingerprint
        self.latest = normalized
        self.latest_cover = cover
        self.publish_snapshot(normalized, cover)
        LOG.info("Published MPRIS metadata for track %s", text_value(normalized["title"]))


async def get_metadata(bus):
    reply = await bus.call(
        Message(
            destination=SERVICE,
            path=PLAYER_PATH,
            interface=PROPERTIES_INTERFACE,
            member="Get",
            signature="ss",
            body=[PLAYER_INTERFACE, "Metadata"],
        )
    )
    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(f"Could not read MPRIS Metadata: {reply.error_name}")
    return reply.body[0].value


async def run():
    bridge = Bridge()
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    loop = asyncio.get_running_loop()

    async def refresh(metadata):
        try:
            await bridge.update(metadata)
        except Exception:
            LOG.exception("Could not publish Shairport MPRIS metadata")

    def on_message(message):
        if (
            message.message_type == MessageType.SIGNAL
            and message.path == PLAYER_PATH
            and message.interface == PROPERTIES_INTERFACE
            and message.member == "PropertiesChanged"
            and message.body
            and message.body[0] == PLAYER_INTERFACE
        ):
            changed = message.body[1]
            if "Metadata" in changed:
                asyncio.run_coroutine_threadsafe(refresh(changed["Metadata"].value), loop)
        return False

    match = (
        "type='signal',path='{}',interface='{}',member='PropertiesChanged'"
        .format(PLAYER_PATH, PROPERTIES_INTERFACE)
    )
    reply = await bus.call(
        Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="AddMatch",
            signature="s",
            body=[match],
        )
    )
    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(f"Could not subscribe to MPRIS updates: {reply.error_name}")
    bus.add_message_handler(on_message)

    try:
        await refresh(await get_metadata(bus))
        await asyncio.Future()
    finally:
        bus.disconnect()
        bridge.client.disconnect()
        bridge.client.loop_stop()


if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(run())
