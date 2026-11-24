import logging
import os
import signal
import subprocess
import threading
import time

import paho.mqtt.client as mqtt

MQTT_HOST = os.environ.get("MQTT_HOST", "homeassistant.local")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_KEEPALIVE = 60
MQTT_USERNAME = os.environ.get("MQTT_USER")
MQTT_PASSWORD = os.environ.get("MQTT_PASS")

USB_DEVICE_ID = os.environ.get("DAC_USB_ID", "152a:889b").lower()
USB_MATCH_TEXT = os.environ.get("DAC_MATCH_TEXT", "Fosi Audio ZD3").lower()
POLL_INTERVAL_SECONDS = float(os.environ.get("DAC_POLL_INTERVAL_SECONDS", "5.0"))
MQTT_CLIENT_ID = os.environ.get("DAC_MQTT_CLIENT_ID", "aswitch-dac-status")

DAC_STATE_TOPIC = os.environ.get("DAC_STATE_TOPIC", "aswitch/dac/zd3/state")
DAC_DETAILS_TOPIC = os.environ.get("DAC_DETAILS_TOPIC", "aswitch/dac/zd3/details")
DAC_AVAILABILITY_TOPIC = os.environ.get("DAC_AVAILABILITY_TOPIC", "aswitch/dac/zd3/availability")
DAC_COMMAND_TOPIC = os.environ.get("DAC_COMMAND_TOPIC", "")
# Home Assistant publishes "online" here on every start. State is published
# only on change, so after its restart on 2026-11-22 it showed the ZD3 as off
# while it was connected. Republish everything when it comes back, and on a
# timer so a lost retained value heals by itself. 0 disables the timer.
HA_STATUS_TOPIC = os.environ.get("HA_STATUS_TOPIC", "homeassistant/status")
REPUBLISH_SECONDS = float(os.environ.get("DAC_REPUBLISH_SECONDS", "300"))


def normalize_lines(output):
    return [line.strip() for line in output.splitlines() if line.strip()]


def detect_dac():
    result = subprocess.run(
        ["lsusb"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "lsusb failed")

    lines = normalize_lines(result.stdout)
    for line in lines:
        line_lower = line.lower()
        if f"id {USB_DEVICE_ID}" in line_lower or USB_MATCH_TEXT in line_lower:
            return True, line
    return False, ""


class DacStatusPublisher:
    def __init__(self, clock=time.monotonic):
        self.logger = logging.getLogger("dac_status")
        self._stop = threading.Event()
        self._clock = clock
        self.last_state = None
        self.last_details = None
        self.last_full_publish = clock()
        self.client = self._build_client()

    def _build_client(self):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
        if MQTT_USERNAME:
            client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        client.will_set(DAC_AVAILABILITY_TOPIC, "offline", retain=True)
        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        return client

    def on_connect(self, client, userdata, flags, reason_code, properties):
        self.logger.info("Connected to MQTT: reason_code=%s", reason_code)
        self.publish_availability("online")
        client.subscribe(HA_STATUS_TOPIC)
        self.force_republish()
        if DAC_COMMAND_TOPIC:
            client.subscribe(DAC_COMMAND_TOPIC)
            self.logger.info("Subscribed to command topic: %s", DAC_COMMAND_TOPIC)

    def on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="ignore").strip().lower()
        if msg.topic == HA_STATUS_TOPIC:
            if payload == "online":
                self.logger.info("Home Assistant is online; republishing availability and state")
                self.publish_availability("online")
                self.force_republish()
            return
        self.logger.info("Command %s -> %s", msg.topic, payload)
        if payload == "start":
            self._trigger_camilladsp()

    def _trigger_camilladsp(self):
        self.logger.info("Triggering camilladsp.service start")
        result = subprocess.run(
            ["sudo", "systemctl", "start", "--no-block", "camilladsp.service"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.logger.warning("systemctl start failed: %s", result.stderr.strip())

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        if self._stop.is_set():
            self.logger.info("Disconnected from MQTT")
            return
        self.logger.warning("MQTT disconnected: reason_code=%s", reason_code)

    def force_republish(self):
        """Make the next poll publish state and details even if unchanged."""
        self.last_state = None
        self.last_details = None

    def republish_if_due(self):
        if REPUBLISH_SECONDS <= 0:
            return
        now = self._clock()
        if now - self.last_full_publish >= REPUBLISH_SECONDS:
            self.last_full_publish = now
            self.publish_availability("online")
            self.force_republish()

    def publish_availability(self, value):
        self.client.publish(DAC_AVAILABILITY_TOPIC, value, retain=True)

    def publish_state(self, connected, details):
        state = "connected" if connected else "disconnected"
        if state != self.last_state:
            self.client.publish(DAC_STATE_TOPIC, state, retain=True)
            self.logger.info("Published %s -> %s", DAC_STATE_TOPIC, state)
            self.last_state = state

        normalized_details = details if connected else ""
        if normalized_details != self.last_details:
            self.client.publish(DAC_DETAILS_TOPIC, normalized_details, retain=True)
            if connected:
                self.logger.info("Published %s -> %s", DAC_DETAILS_TOPIC, normalized_details)
            self.last_details = normalized_details

    def run(self):
        self.client.connect(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
        self.client.loop_start()
        self.logger.info(
            "Watching DAC via lsusb (id=%s, text=%s) every %.1fs",
            USB_DEVICE_ID,
            USB_MATCH_TEXT,
            POLL_INTERVAL_SECONDS,
        )

        try:
            while not self._stop.is_set():
                try:
                    self.republish_if_due()
                    connected, details = detect_dac()
                    self.publish_state(connected, details)
                except Exception:
                    self.logger.exception("DAC detection failed")
                self._stop.wait(POLL_INTERVAL_SECONDS)
        finally:
            self.publish_availability("offline")
            self.client.loop_stop()
            self.client.disconnect()

    def stop(self, signum=None, frame=None):
        self._stop.set()


def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = DacStatusPublisher()
    signal.signal(signal.SIGINT, app.stop)
    signal.signal(signal.SIGTERM, app.stop)
    app.run()


if __name__ == "__main__":
    main()
