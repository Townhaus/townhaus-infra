#!/usr/bin/python3
"""Power the USB DAC and wait for its ALSA card before Shairport Sync opens it.

Shairport Sync runs this as `run_this_before_play_begins` with
`wait_for_completion = "yes"`. Shairport opens the ALSA device as soon as play
begins and exits on ENODEV when the card is missing, so the DAC has to be
powered and enumerated first. Home Assistant only learns about the session from
Shairport's MQTT play_start event, which is too late to win that race.

When the card is already present this returns immediately, without MQTT.
Otherwise it asks the amp trigger relay to switch on over MQTT and waits for the
card. Home Assistant still chooses the DAC input and owns power-off.
"""

import base64
import sys
import time
from pathlib import Path

DEFAULT_CONFIG_PATH = "/etc/default/shairport-dac-wake"


def log(message):
    print(f"shairport-dac-wake: {message}", file=sys.stderr, flush=True)


def load_config(path):
    config = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    return config


def card_present(card, asound_root="/proc/asound"):
    # ALSA exposes each card's id as a /proc/asound/<id> symlink to cardN.
    return (Path(asound_root) / card).exists()


def request_power(config, mqtt=None):
    if mqtt is None:
        import paho.mqtt.client as mqtt

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=config.get("MQTT_CLIENT_ID", "shairport-dac-wake"),
    )
    client.username_pw_set(
        base64.b64decode(config["MQTT_USERNAME_B64"]).decode(),
        base64.b64decode(config["MQTT_PASSWORD_B64"]).decode(),
    )
    client.connect(config["MQTT_HOST"], int(config["MQTT_PORT"]), 10)
    client.loop_start()
    try:
        info = client.publish(config["DAC_WAKE_TOPIC"], config.get("DAC_WAKE_PAYLOAD", "on"), qos=1)
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            raise RuntimeError(f"MQTT publish to {config['DAC_WAKE_TOPIC']} was not acknowledged")
    finally:
        client.loop_stop()
        client.disconnect()


def wait_for_card(card, timeout, present=card_present, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    while True:
        if present(card):
            return True
        if clock() >= deadline:
            return False
        sleep(0.25)


def main(config_path=None, present=card_present, power=request_power, wait=wait_for_card):
    config = load_config(config_path or DEFAULT_CONFIG_PATH)
    card = config["DAC_WAKE_CARD"]
    if present(card):
        return 0

    started = time.monotonic()
    log(f"ALSA card {card} is missing; requesting DAC power")
    try:
        power(config)
    except Exception as error:  # Home Assistant may still power it from play_start.
        log(f"power request failed: {error}")

    timeout = float(config.get("DAC_WAKE_TIMEOUT_SECONDS", "20"))
    if wait(card, timeout, present=present):
        log(f"ALSA card {card} appeared after {time.monotonic() - started:.1f}s")
        return 0
    log(f"ALSA card {card} did not appear within {timeout:g}s")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
