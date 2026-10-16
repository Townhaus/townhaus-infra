import json
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np

import audio_activity as audio


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.monitor = audio.AudioActivityMonitor()

    def test_scarlett_pair_feeds_detection_recording_and_ingest(self):
        # Unselected inputs are loud enough to distort both detection and recordings.
        captured = np.array([[30000, 30000, 1000, -2000, 30000, 30000]], dtype=np.int16)
        expected = captured[:, [2, 3]]
        self.monitor.recording_writer = Mock()
        self.monitor.groovenet_ingest = Mock()
        self.monitor.is_active = True
        stream = Mock()

        def read(_):
            self.monitor.stop_event.set()
            return captured, True

        stream.read.side_effect = read
        with patch.object(audio, "AUDIO_CHANNEL_MAP", (2, 3)):
            self.monitor.capture_loop(stream)
        self.monitor.recording_writer.enqueue_audio.assert_called_once_with(expected.tobytes())
        np.testing.assert_array_equal(
            self.monitor.groovenet_ingest.write.call_args.args[0], expected
        )
        expected_rms = float(np.sqrt(np.mean(expected.astype(np.float64) ** 2)) / audio.INT16_MAX)
        self.assertAlmostEqual(self.monitor.last_rms, expected_rms)
        self.assertEqual(self.monitor.overflow_count, 1)

    def test_default_stereo_pair_is_preserved(self):
        captured = np.array([[1000, -2000], [2000, -3000]], dtype=np.int16)
        self.monitor.recording_writer = Mock()
        stream = Mock()

        def read(_):
            self.monitor.stop_event.set()
            return captured, False

        stream.read.side_effect = read
        self.monitor.capture_loop(stream)
        self.monitor.recording_writer.enqueue_audio.assert_called_once_with(captured.tobytes())

    def test_invalid_selection_fails_before_opening_device(self):
        for selection in [(-1, 1), (2, 6), (2,), (0, 1, 2)]:
            with self.subTest(selection=selection):
                with patch.object(audio, "AUDIO_CAPTURE_CHANNELS", 6):
                    with patch.object(audio, "AUDIO_CHANNEL_MAP", selection):
                        with self.assertLogs("audio_activity", level="ERROR"):
                            with self.assertRaises(SystemExit):
                                self.monitor.resolve_input_settings()

    def test_capture_requires_all_requested_hardware_channels(self):
        device = {"max_input_channels": 2, "default_samplerate": 44100}
        with patch.object(audio.sd, "query_devices", return_value=device):
            with patch.object(audio, "AUDIO_CAPTURE_CHANNELS", 6):
                with patch.object(audio, "AUDIO_CHANNEL_MAP", (2, 3)):
                    with self.assertLogs("audio_activity", level="ERROR"):
                        with self.assertRaises(SystemExit):
                            self.monitor.resolve_input_settings()


class IdentityTests(unittest.TestCase):
    def test_sources_have_separate_topics_and_broker_identities(self):
        script = """
import json
import audio_activity as audio
import recording_archive as archive
print(json.dumps([
    audio.AudioActivityMonitor().client._client_id.decode(),
    audio.AUDIO_ACTIVITY_STATE_TOPIC,
    audio.RECORDING_COMMAND_TOPIC,
    archive.TOPIC,
]))
"""
        for source in ["aswitch", "pi-cam"]:
            environment = dict(os.environ)
            environment.pop("AUDIO_MQTT_TOPIC_PREFIX", None)
            if source == "pi-cam":
                environment["AUDIO_MQTT_TOPIC_PREFIX"] = source
            result = subprocess.check_output(
                [sys.executable, "-c", script], env=environment, text=True
            )
            self.assertEqual(
                json.loads(result),
                [
                    f"{source}-audio-activity",
                    f"{source}/audio_activity/state",
                    f"{source}/audio_recording/set",
                    f"{source}/audio_recording/archive",
                ],
            )


if __name__ == "__main__":
    unittest.main()
