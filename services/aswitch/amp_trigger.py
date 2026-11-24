import logging
import os
import signal
import threading

import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO

TRIGGER_PIN = int(os.environ.get("AMP_TRIGGER_GPIO", "27"))

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_KEEPALIVE = 60
MQTT_USERNAME = os.environ.get("MQTT_USER")
MQTT_PASSWORD = os.environ.get("MQTT_PASS")

TRIGGER_COMMAND_TOPIC = os.environ.get("AMP_TRIGGER_COMMAND_TOPIC", "pi-cam/trigger")
TRIGGER_STATE_TOPIC = os.environ.get("AMP_TRIGGER_STATE_TOPIC", "pi-cam/trigger/state")
AVAILABILITY_TOPIC = os.environ.get("AMP_TRIGGER_AVAILABILITY_TOPIC", "pi-cam/availability")
# A fixed client ID, so a reconnect takes over the old session at CONNECT time.
# With a random ID, a half-open old session can time out after the new one has
# published "online", and its "offline" will then overwrites the retained value.
MQTT_CLIENT_ID = os.environ.get("AMP_TRIGGER_MQTT_CLIENT_ID", "pi-cam-amp-trigger")
# Home Assistant publishes "online" here on every start. Its restart on
# 2026-11-22 left this relay unavailable for two days because nothing here
# republished, and the ZD3 wake sequence refuses to run on an unavailable relay.
HA_STATUS_TOPIC = os.environ.get("HA_STATUS_TOPIC", "homeassistant/status")
# Republish availability and state on a timer too, so any lost retained value
# heals within this many seconds. 0 disables it.
REPUBLISH_SECONDS = float(os.environ.get("AMP_TRIGGER_REPUBLISH_SECONDS", "300"))

# The Tongling / JQC-3FF relay is active high.
TRIGGER_ON = GPIO.HIGH
TRIGGER_OFF = GPIO.LOW


logger = logging.getLogger("amp_trigger")


def normalize_payload(payload):
    return payload.decode(errors="ignore").strip().lower()


def publish_state(client, topic, state):
    info = client.publish(topic, state, retain=True)
    logger.info("Published %s -> %s (mid=%s)", topic, state, info.mid)


def current_trigger_state():
    return "on" if GPIO.input(TRIGGER_PIN) == TRIGGER_ON else "off"


def set_trigger(client, state, publish=True):
    if state not in {"on", "off"}:
        logger.warning("Ignoring invalid trigger command: %s", state)
        return

    current = current_trigger_state()
    if current == state:
        logger.info("Trigger already %s", state)
        if publish:
            publish_state(client, TRIGGER_STATE_TOPIC, current)
        return

    GPIO.output(TRIGGER_PIN, TRIGGER_ON if state == "on" else TRIGGER_OFF)
    new_state = current_trigger_state()
    logger.info("Trigger changed: %s -> %s", current, new_state)
    if publish:
        publish_state(client, TRIGGER_STATE_TOPIC, new_state)


def publish_online(client):
    client.publish(AVAILABILITY_TOPIC, "online", retain=True)
    publish_state(client, TRIGGER_STATE_TOPIC, current_trigger_state())


def on_connect(client, userdata, flags, reason_code, properties):
    logger.info("Connected to MQTT: reason_code=%s", reason_code)
    client.subscribe(TRIGGER_COMMAND_TOPIC)
    client.subscribe(HA_STATUS_TOPIC)
    publish_online(client)


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    if reason_code.value == 0:
        logger.info("Disconnected from MQTT")
    else:
        logger.warning("MQTT disconnected unexpectedly: reason_code=%s", reason_code)


def on_message(client, userdata, msg):
    payload = normalize_payload(msg.payload)
    logger.info("Incoming %s -> %s", msg.topic, payload)
    if msg.topic == HA_STATUS_TOPIC:
        if payload == "online":
            logger.info("Home Assistant is online; republishing availability and state")
            publish_online(client)
        return
    if msg.topic == TRIGGER_COMMAND_TOPIC:
        set_trigger(client, payload)


def republish_loop(client, stop, interval=REPUBLISH_SECONDS):
    """Republish availability and state every `interval` seconds until `stop` is set."""
    if interval <= 0:
        stop.wait()
        return
    while not stop.wait(interval):
        if client.is_connected():
            publish_online(client)


def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(TRIGGER_PIN, GPIO.OUT, initial=TRIGGER_OFF)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.will_set(AVAILABILITY_TOPIC, "offline", retain=True)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    stop = threading.Event()

    def shutdown(signum=None, frame=None):
        logger.info("Shutting down")
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        client.connect(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
        client.loop_start()
        republish_loop(client, stop)
    finally:
        if client.is_connected():
            # disconnect() is clean, so the broker won't send the will for us.
            client.publish(AVAILABILITY_TOPIC, "offline", retain=True).wait_for_publish(timeout=2)
        client.loop_stop()
        client.disconnect()
        GPIO.output(TRIGGER_PIN, TRIGGER_OFF)
        logger.info("Applied safe default: trigger=off")
        GPIO.cleanup()


if __name__ == "__main__":
    main()
