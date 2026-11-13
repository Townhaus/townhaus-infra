#!/usr/bin/env python3
"""Exercise the Shairport DAC wake hook offline: no MQTT broker, ALSA or Pi needed."""

import base64
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "ansible/roles/shairport/files/shairport_dac_wake.py"
spec = importlib.util.spec_from_file_location("shairport_dac_wake", HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def b64(value):
    return base64.b64encode(value.encode()).decode()


CONFIG = f"""# rendered by Ansible
MQTT_HOST=homeassistant.local
MQTT_PORT=1883
MQTT_CLIENT_ID=shairport-dac-wake
MQTT_USERNAME_B64={b64("user")}
MQTT_PASSWORD_B64={b64("p=ss word")}
DAC_WAKE_CARD=ZD3
DAC_WAKE_TOPIC=pi-cam/trigger
DAC_WAKE_PAYLOAD=on
DAC_WAKE_TIMEOUT_SECONDS=3
"""


class FakeInfo:
    def __init__(self, published):
        self.published = published

    def wait_for_publish(self, timeout):
        self.timeout = timeout

    def is_published(self):
        return self.published


class FakeMqtt:
    class CallbackAPIVersion:
        VERSION2 = "v2"

    def __init__(self, published=True):
        self.published = published
        self.calls = []
        fake = self

        class Client:
            def __init__(self, api, client_id):
                fake.calls.append(("init", api, client_id))

            def username_pw_set(self, user, password):
                fake.calls.append(("auth", user, password))

            def connect(self, host, port, keepalive):
                fake.calls.append(("connect", host, port, keepalive))

            def loop_start(self):
                fake.calls.append(("loop_start",))

            def publish(self, topic, payload, qos):
                fake.calls.append(("publish", topic, payload, qos))
                return FakeInfo(fake.published)

            def loop_stop(self):
                fake.calls.append(("loop_stop",))

            def disconnect(self):
                fake.calls.append(("disconnect",))

        self.Client = Client


class DacWakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmp.name) / "shairport-dac-wake"
        self.config_path.write_text(CONFIG)
        self.addCleanup(self.tmp.cleanup)

    def test_load_config_keeps_values_containing_equals(self):
        config = hook.load_config(self.config_path)
        self.assertEqual(config["DAC_WAKE_CARD"], "ZD3")
        self.assertEqual(base64.b64decode(config["MQTT_PASSWORD_B64"]).decode(), "p=ss word")
        self.assertNotIn("# rendered by Ansible", config)

    def test_card_present_reads_proc_asound_ids(self):
        root = Path(self.tmp.name) / "asound"
        root.mkdir()
        self.assertFalse(hook.card_present("ZD3", root))
        (root / "card1").mkdir()
        (root / "ZD3").symlink_to("card1")
        self.assertTrue(hook.card_present("ZD3", root))

    def test_present_card_skips_mqtt(self):
        powered = []
        result = hook.main(self.config_path, present=lambda card: True, power=powered.append)
        self.assertEqual(result, 0)
        self.assertEqual(powered, [])

    def test_missing_card_powers_relay_and_waits(self):
        powered, waited = [], []

        def wait(card, timeout, present):
            waited.append((card, timeout))
            return True

        result = hook.main(
            self.config_path, present=lambda card: False, power=powered.append, wait=wait
        )
        self.assertEqual(result, 0)
        self.assertEqual(powered[0]["DAC_WAKE_TOPIC"], "pi-cam/trigger")
        self.assertEqual(waited, [("ZD3", 3.0)])

    def test_power_failure_still_waits_for_home_assistant(self):
        def power(config):
            raise OSError("broker unreachable")

        result = hook.main(
            self.config_path,
            present=lambda card: False,
            power=power,
            wait=lambda card, timeout, present: True,
        )
        self.assertEqual(result, 0)

    def test_card_that_never_appears_fails(self):
        result = hook.main(
            self.config_path,
            present=lambda card: False,
            power=lambda config: None,
            wait=lambda card, timeout, present: False,
        )
        self.assertEqual(result, 1)

    def test_wait_for_card_polls_until_present_or_deadline(self):
        now = [0.0]
        answers = iter([False, False, True])
        self.assertTrue(
            hook.wait_for_card(
                "ZD3",
                5,
                present=lambda card: next(answers),
                clock=lambda: now[0],
                sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            )
        )
        now[0] = 0.0
        self.assertFalse(
            hook.wait_for_card(
                "ZD3",
                1,
                present=lambda card: False,
                clock=lambda: now[0],
                sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            )
        )
        self.assertGreaterEqual(now[0], 1)

    def test_request_power_publishes_relay_on_with_decoded_credentials(self):
        mqtt = FakeMqtt()
        hook.request_power(hook.load_config(self.config_path), mqtt=mqtt)
        self.assertIn(("auth", "user", "p=ss word"), mqtt.calls)
        self.assertIn(("connect", "homeassistant.local", 1883, 10), mqtt.calls)
        self.assertIn(("publish", "pi-cam/trigger", "on", 1), mqtt.calls)
        self.assertEqual(mqtt.calls[-1], ("disconnect",))

    def test_request_power_raises_when_publish_is_not_acknowledged(self):
        mqtt = FakeMqtt(published=False)
        with self.assertRaises(RuntimeError):
            hook.request_power(hook.load_config(self.config_path), mqtt=mqtt)
        self.assertEqual(mqtt.calls[-1], ("disconnect",))


if __name__ == "__main__":
    unittest.main()
