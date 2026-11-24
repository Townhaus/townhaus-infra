"""amp_trigger and dac_status republish when Home Assistant comes back.

Home Assistant's restart on 2026-11-22 left the pi-cam relay unavailable and
the ZD3 shown as off for two days, because both services published only on
connect or on change. These cover the resync on `homeassistant/status` and the
timed republish.
"""

import sys
import threading
import types
import unittest
from unittest.mock import MagicMock


def _install_fake_gpio():
    gpio = types.ModuleType("RPi.GPIO")
    gpio.HIGH, gpio.LOW, gpio.BCM, gpio.OUT = 1, 0, "BCM", "OUT"
    gpio.level = 0
    gpio.input = lambda pin: gpio.level
    gpio.output = lambda pin, value: setattr(gpio, "level", value)
    for name in ("setmode", "setwarnings", "setup", "cleanup"):
        setattr(gpio, name, lambda *a, **k: None)
    rpi = types.ModuleType("RPi")
    rpi.GPIO = gpio
    sys.modules.setdefault("RPi", rpi)
    sys.modules.setdefault("RPi.GPIO", gpio)
    return sys.modules["RPi.GPIO"]


GPIO = _install_fake_gpio()

import amp_trigger  # noqa: E402
import dac_status  # noqa: E402


def message(topic, payload):
    return types.SimpleNamespace(topic=topic, payload=payload.encode())


def published(client):
    return [(c.args[0], c.args[1]) for c in client.publish.call_args_list]


class AmpTriggerResyncTests(unittest.TestCase):
    def setUp(self):
        GPIO.level = GPIO.LOW
        self.client = MagicMock()

    def test_connect_subscribes_to_ha_status_and_publishes_online(self):
        amp_trigger.on_connect(self.client, None, None, 0, None)
        topics = [c.args[0] for c in self.client.subscribe.call_args_list]
        self.assertIn(amp_trigger.HA_STATUS_TOPIC, topics)
        self.assertIn(amp_trigger.TRIGGER_COMMAND_TOPIC, topics)
        self.assertEqual(
            published(self.client),
            [(amp_trigger.AVAILABILITY_TOPIC, "online"), (amp_trigger.TRIGGER_STATE_TOPIC, "off")],
        )

    def test_ha_online_republishes_availability_and_current_state(self):
        GPIO.level = GPIO.HIGH
        amp_trigger.on_message(self.client, None, message(amp_trigger.HA_STATUS_TOPIC, "online"))
        self.assertEqual(
            published(self.client),
            [(amp_trigger.AVAILABILITY_TOPIC, "online"), (amp_trigger.TRIGGER_STATE_TOPIC, "on")],
        )
        for call in self.client.publish.call_args_list:
            self.assertTrue(call.kwargs.get("retain"))

    def test_ha_offline_is_ignored_and_never_switches_the_relay(self):
        amp_trigger.on_message(self.client, None, message(amp_trigger.HA_STATUS_TOPIC, "offline"))
        self.client.publish.assert_not_called()
        self.assertEqual(GPIO.level, GPIO.LOW)

    def test_trigger_command_still_switches_the_relay(self):
        amp_trigger.on_message(self.client, None, message(amp_trigger.TRIGGER_COMMAND_TOPIC, "ON"))
        self.assertEqual(GPIO.level, GPIO.HIGH)
        self.assertIn((amp_trigger.TRIGGER_STATE_TOPIC, "on"), published(self.client))

    def test_republish_loop_publishes_while_connected_and_stops(self):
        stop = threading.Event()
        self.client.is_connected.side_effect = [True, False, True]
        waits = iter([False, False, False, True])
        stop.wait = lambda timeout=None: next(waits)
        amp_trigger.republish_loop(self.client, stop, interval=300)
        availability = [p for p in published(self.client) if p[0] == amp_trigger.AVAILABILITY_TOPIC]
        self.assertEqual(len(availability), 2)  # skipped the tick while disconnected

    def test_republish_loop_disabled_just_waits_for_stop(self):
        stop = threading.Event()
        stop.set()
        amp_trigger.republish_loop(self.client, stop, interval=0)
        self.client.publish.assert_not_called()

    def test_fixed_client_id_default(self):
        self.assertEqual(amp_trigger.MQTT_CLIENT_ID, "pi-cam-amp-trigger")


class DacStatusResyncTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.client = MagicMock()
        self.app = dac_status.DacStatusPublisher(clock=lambda: self.now)
        self.app.client = self.client

    def test_unchanged_state_is_published_once(self):
        self.app.publish_state(True, "ZD3")
        self.app.publish_state(True, "ZD3")
        states = [p for p in published(self.client) if p[0] == dac_status.DAC_STATE_TOPIC]
        self.assertEqual(states, [(dac_status.DAC_STATE_TOPIC, "connected")])

    def test_ha_online_republishes_availability_and_unchanged_state(self):
        self.app.publish_state(True, "ZD3")
        self.client.reset_mock()
        self.app.on_message(self.client, None, message(dac_status.HA_STATUS_TOPIC, "online"))
        self.assertEqual(published(self.client), [(dac_status.DAC_AVAILABILITY_TOPIC, "online")])
        self.app.publish_state(True, "ZD3")
        self.assertIn((dac_status.DAC_STATE_TOPIC, "connected"), published(self.client))
        self.assertIn((dac_status.DAC_DETAILS_TOPIC, "ZD3"), published(self.client))

    def test_ha_status_never_triggers_camilladsp(self):
        self.app._trigger_camilladsp = MagicMock()
        self.app.on_message(self.client, None, message(dac_status.HA_STATUS_TOPIC, "start"))
        self.app._trigger_camilladsp.assert_not_called()

    def test_connect_subscribes_to_ha_status_and_forces_state(self):
        self.app.publish_state(False, "")
        self.app.on_connect(self.client, None, None, 0, None)
        topics = [c.args[0] for c in self.client.subscribe.call_args_list]
        self.assertIn(dac_status.HA_STATUS_TOPIC, topics)
        self.assertIsNone(self.app.last_state)

    def test_timed_republish_only_when_due(self):
        self.app.publish_state(True, "ZD3")
        self.client.reset_mock()
        self.now += dac_status.REPUBLISH_SECONDS - 1
        self.app.republish_if_due()
        self.client.publish.assert_not_called()
        self.now += 1
        self.app.republish_if_due()
        self.assertEqual(published(self.client), [(dac_status.DAC_AVAILABILITY_TOPIC, "online")])
        self.app.publish_state(True, "ZD3")
        self.assertIn((dac_status.DAC_STATE_TOPIC, "connected"), published(self.client))


if __name__ == "__main__":
    unittest.main()
