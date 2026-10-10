import logging
import math
import os
import queue
import re
import signal
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import paho.mqtt.client as mqtt
import sounddevice as sd

from groovenet_ingest import GroovenetIngest

AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE", "plughw:CARD=CODEC,DEV=0")
MQTT_HOST = os.environ.get("MQTT_HOST", "homeassistant.local")
MQTT_PORT = 1883
MQTT_KEEPALIVE = 60
MQTT_USERNAME = os.environ.get("MQTT_USER")
MQTT_PASSWORD = os.environ.get("MQTT_PASS")
RECORDINGS_DIR = Path(os.environ.get("RECORDINGS_DIR", "./recordings"))
RECORDING_ATTENUATION_DB = float(os.environ.get("RECORDING_ATTENUATION_DB", "0.0"))
GROOVENET_INGEST_ENABLED = os.environ.get("GROOVENET_INGEST_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
GROOVENET_SAMPLE_RATE = int(os.environ.get("GROOVENET_SAMPLE_RATE", "44100"))
GROOVENET_INGEST_ONLY_WHEN_ACTIVE = os.environ.get(
    "GROOVENET_INGEST_ONLY_WHEN_ACTIVE", "false"
).lower() in {"1", "true", "yes", "on"}

AUDIO_ACTIVITY_STATE_TOPIC = "aswitch/audio_activity/state"
AUDIO_ACTIVITY_RMS_TOPIC = "aswitch/audio_activity/rms"
RECORDING_COMMAND_TOPIC = "aswitch/audio_recording/set"
RECORDING_STATE_TOPIC = "aswitch/audio_recording/state"
RECORDING_FILE_TOPIC = "aswitch/audio_recording/file"
RECORDING_ERROR_TOPIC = "aswitch/audio_recording/error"

RMS_THRESHOLD = 0.01
ACTIVE_HOLD_SECONDS = float(os.environ.get("AUDIO_ACTIVITY_ACTIVE_HOLD_SECONDS", "2.0"))
INACTIVE_HOLD_SECONDS = float(
    os.environ.get("AUDIO_ACTIVITY_INACTIVE_HOLD_SECONDS", "300.0")
)
PUBLISH_RMS_DEBUG = True
STATUS_LOG_INTERVAL_SECONDS = 30.0
RMS_PUBLISH_INTERVAL_SECONDS = 1.0
OVERFLOW_LOG_INTERVAL_SECONDS = 30.0

CHANNELS = 2
BLOCKSIZE = 8192
DTYPE = "int16"
WAV_SAMPLE_WIDTH_BYTES = 2
MAIN_LOOP_INTERVAL_SECONDS = 0.5
STREAM_LATENCY = "high"
RECORDING_QUEUE_BLOCKS = 256
RECORDING_ATTENUATION_LINEAR = 10 ** (RECORDING_ATTENUATION_DB / 20.0)
INT16_MAX = float(np.iinfo(np.int16).max)


def normalize_payload(payload):
    return payload.decode(errors="ignore").strip().lower()


class RecordingWriter(threading.Thread):
    def __init__(self, logger, output_dir, channels, sample_rate):
        super().__init__(name="recording-writer", daemon=True)
        self.logger = logger
        self.output_dir = Path(output_dir)
        self.channels = channels
        self.sample_rate = sample_rate
        self.queue = queue.Queue(maxsize=RECORDING_QUEUE_BLOCKS)
        self.stop_event = threading.Event()
        self.recording_enabled = False
        self.current_wave = None
        self.current_path = None
        self.state_lock = threading.Lock()
        self.dropped_blocks = 0
        self.last_error = None

    def run(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)

        while not self.stop_event.is_set() or not self.queue.empty():
            try:
                item = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                action = item[0]
                if action == "set_recording":
                    self._set_recording(item[1])
                elif action == "audio":
                    self._write_audio(item[1])
                elif action == "stop":
                    self._close_wave_file()
                    self.stop_event.set()
            except Exception as exc:
                with self.state_lock:
                    self.last_error = str(exc)
                self.logger.exception("Recording writer error")
                self._close_wave_file(finalize=False)
                with self.state_lock:
                    self.recording_enabled = False
            finally:
                self.queue.task_done()

        self._close_wave_file()

    def enqueue_audio(self, pcm_bytes):
        with self.state_lock:
            if not self.recording_enabled:
                return
        try:
            self.queue.put_nowait(("audio", pcm_bytes))
        except queue.Full:
            with self.state_lock:
                self.dropped_blocks += 1

    def set_recording(self, enabled):
        with self.state_lock:
            self.recording_enabled = enabled
        self.queue.put(("set_recording", enabled))

    def stop(self):
        self.queue.put(("stop", None))
        self.join(timeout=5)

    def recording_state(self):
        with self.state_lock:
            return self.recording_enabled

    def current_file(self):
        with self.state_lock:
            return str(self.current_path) if self.current_path else ""

    def consume_dropped_blocks(self):
        with self.state_lock:
            dropped_blocks = self.dropped_blocks
            self.dropped_blocks = 0
        return dropped_blocks

    def consume_last_error(self):
        with self.state_lock:
            error = self.last_error
            self.last_error = None
        return error

    def _set_recording(self, enabled):
        if enabled:
            if self.current_wave is None:
                self._open_wave_file()
        else:
            self._close_wave_file()

    def _open_wave_file(self):
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = self.output_dir / f"audio-{timestamp}.wav.part"
        wave_file = wave.open(str(path), "wb")
        wave_file.setnchannels(self.channels)
        wave_file.setsampwidth(WAV_SAMPLE_WIDTH_BYTES)
        wave_file.setframerate(self.sample_rate)
        with self.state_lock:
            self.current_wave = wave_file
            self.current_path = path
        self.logger.info("Recording started: %s", path)

    def _close_wave_file(self, finalize=True):
        with self.state_lock:
            wave_file = self.current_wave
            path = self.current_path
            self.current_wave = None
            self.current_path = None

        if wave_file is not None:
            wave_file.close()
            if finalize:
                completed_path = path.with_suffix("")
                path.replace(completed_path)
                self.logger.info("Recording stopped: %s", completed_path)
            else:
                self.logger.warning("Unfinished recording retained: %s", path)

    def _write_audio(self, pcm_bytes):
        with self.state_lock:
            wave_file = self.current_wave
        if wave_file is not None:
            if RECORDING_ATTENUATION_DB != 0.0:
                pcm_array = np.frombuffer(pcm_bytes, dtype=np.int16)
                scaled = np.clip(
                    pcm_array.astype(np.float32) * RECORDING_ATTENUATION_LINEAR,
                    -32768.0,
                    32767.0,
                )
                pcm_bytes = scaled.astype(np.int16).tobytes()
            wave_file.writeframes(pcm_bytes)


class AudioActivityMonitor:
    def __init__(self):
        self.logger = logging.getLogger("audio_activity")
        self.stop_event = threading.Event()
        self.client = self._build_mqtt_client()

        self.state_lock = threading.Lock()
        self.last_rms = 0.0
        self.above_threshold_since = None
        self.below_threshold_since = time.monotonic()
        self.last_status_log_at = 0.0
        self.last_rms_publish_at = 0.0
        self.last_overflow_log_at = 0.0
        self.is_active = False
        self.input_device = None
        self.input_device_name = None
        self.overflow_count = 0
        self.recording_requested = False

        self.sample_rate = None
        self.recording_writer = None
        self.capture_thread = None
        self.groovenet_ingest = None

    def _build_mqtt_client(self):
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="aswitch-audio-activity",
        )
        if MQTT_USERNAME:
            client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        return client

    def on_connect(self, client, userdata, flags, reason_code, properties):
        self.logger.info("Connected to MQTT: reason_code=%s", reason_code)
        client.subscribe(RECORDING_COMMAND_TOPIC)
        self.publish_activity_state()
        self.publish_recording_state(force=True)

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        if self.stop_event.is_set():
            self.logger.info("Disconnected from MQTT")
            return
        self.logger.warning("MQTT disconnected: reason_code=%s", reason_code)

    def on_message(self, client, userdata, msg):
        payload = normalize_payload(msg.payload)
        if msg.topic != RECORDING_COMMAND_TOPIC:
            return

        if payload == "on":
            self.set_recording_requested(True)
        elif payload == "off":
            self.set_recording_requested(False)
        else:
            self.logger.warning("Ignoring invalid recording command: %s", payload)

    def publish_activity_state(self):
        state = "active" if self.is_active else "inactive"
        info = self.client.publish(AUDIO_ACTIVITY_STATE_TOPIC, state, retain=True)
        self.logger.info("Published %s -> %s (mid=%s)", AUDIO_ACTIVITY_STATE_TOPIC, state, info.mid)

    def publish_recording_state(self, force=False):
        with self.state_lock:
            state = "on" if self.recording_requested else "off"
        info = self.client.publish(RECORDING_STATE_TOPIC, state, retain=True)
        self.logger.info("Published %s -> %s (mid=%s)", RECORDING_STATE_TOPIC, state, info.mid)

        if self.recording_writer is None:
            return

        current_file = self.recording_writer.current_file()
        self.client.publish(RECORDING_FILE_TOPIC, current_file, retain=True)

    def publish_recording_error(self, message):
        self.client.publish(RECORDING_ERROR_TOPIC, message, retain=False)
        self.logger.error("%s", message)

    def publish_rms(self, rms_value):
        if not PUBLISH_RMS_DEBUG:
            return
        now = time.monotonic()
        if now - self.last_rms_publish_at < RMS_PUBLISH_INTERVAL_SECONDS:
            return
        self.last_rms_publish_at = now
        self.client.publish(AUDIO_ACTIVITY_RMS_TOPIC, f"{rms_value:.6f}", retain=False)

    def set_recording_requested(self, enabled):
        with self.state_lock:
            if self.recording_requested == enabled:
                return
            self.recording_requested = enabled
        if self.recording_writer is not None:
            self.recording_writer.set_recording(enabled)
        self.logger.info("Recording command applied: %s", "on" if enabled else "off")
        self.publish_recording_state(force=True)

    def process_audio_block(self, indata, overflowed=False):
        rms_value = float(math.sqrt(np.mean(np.square(indata, dtype=np.float64))) / INT16_MAX)
        now = time.monotonic()
        pcm_bytes = indata.tobytes()

        with self.state_lock:
            if overflowed:
                self.overflow_count += 1
            self.last_rms = rms_value
            if rms_value >= RMS_THRESHOLD:
                if self.above_threshold_since is None:
                    self.above_threshold_since = now
                self.below_threshold_since = None
            else:
                if self.below_threshold_since is None:
                    self.below_threshold_since = now
                self.above_threshold_since = None

        if self.recording_writer is not None:
            self.recording_writer.enqueue_audio(pcm_bytes)
        if self.groovenet_ingest is not None and (
            not GROOVENET_INGEST_ONLY_WHEN_ACTIVE or self.is_active
        ):
            self.groovenet_ingest.write(indata)

    def capture_loop(self, stream):
        while not self.stop_event.is_set():
            try:
                indata, overflowed = stream.read(BLOCKSIZE)
            except sd.PortAudioError as exc:
                if self.stop_event.is_set():
                    return
                self.publish_recording_error(f"Audio capture failed: {exc}")
                self.stop_event.set()
                return

            self.process_audio_block(indata, overflowed=overflowed)

    def update_activity_state(self):
        now = time.monotonic()

        with self.state_lock:
            last_rms = self.last_rms
            above_since = self.above_threshold_since
            below_since = self.below_threshold_since
            current_state = self.is_active
            overflow_count = self.overflow_count
            recording_requested = self.recording_requested

        self.publish_rms(last_rms)

        next_state = current_state

        if not current_state:
            if above_since is not None and now - above_since >= ACTIVE_HOLD_SECONDS:
                next_state = True
        else:
            if below_since is not None and now - below_since >= INACTIVE_HOLD_SECONDS:
                next_state = False

        if next_state != current_state:
            self.is_active = next_state
            self.logger.info(
                "Audio activity changed: %s (rms=%.6f threshold=%.6f)",
                "active" if next_state else "inactive",
                last_rms,
                RMS_THRESHOLD,
            )
            self.publish_activity_state()
            if (
                self.groovenet_ingest is not None
                and GROOVENET_INGEST_ONLY_WHEN_ACTIVE
                and not next_state
            ):
                self.groovenet_ingest.pause()
        elif now - self.last_status_log_at >= STATUS_LOG_INTERVAL_SECONDS:
            self.last_status_log_at = now
            self.logger.info(
                "Audio monitor heartbeat: state=%s rms=%.6f threshold=%.6f recording=%s",
                "active" if current_state else "inactive",
                last_rms,
                RMS_THRESHOLD,
                "on" if recording_requested else "off",
            )

        if overflow_count and now - self.last_overflow_log_at >= OVERFLOW_LOG_INTERVAL_SECONDS:
            self.last_overflow_log_at = now
            self.logger.warning("Audio input overflow count=%s", overflow_count)

        if self.recording_writer is not None:
            dropped_blocks = self.recording_writer.consume_dropped_blocks()
            if dropped_blocks:
                message = f"Recording queue overflow: dropped_blocks={dropped_blocks}"
                self.publish_recording_error(message)
            writer_error = self.recording_writer.consume_last_error()
            if writer_error:
                with self.state_lock:
                    self.recording_requested = False
                self.publish_recording_error(f"Recording writer failed: {writer_error}")
                self.publish_recording_state(force=True)

    def list_input_devices(self):
        devices = []
        for index, device in enumerate(sd.query_devices()):
            if device["max_input_channels"] < 1:
                continue
            devices.append((index, device))
        return devices

    def log_available_input_devices(self):
        devices = self.list_input_devices()
        if not devices:
            self.logger.error("No PortAudio input devices are available")
            return

        self.logger.error("Available PortAudio input devices:")
        for index, device in devices:
            self.logger.error(
                "  [%s] %s (inputs=%s, default_samplerate=%s)",
                index,
                device["name"],
                device["max_input_channels"],
                int(device["default_samplerate"]),
            )

    def find_matching_input_device(self, configured_device):
        card_match = re.search(r"CARD=([^,]+)", configured_device, flags=re.IGNORECASE)
        dev_match = re.search(r"DEV=(\d+)", configured_device, flags=re.IGNORECASE)
        card_token = card_match.group(1).lower() if card_match else None
        dev_token = dev_match.group(1) if dev_match else None
        query = configured_device.lower()

        matches = []
        for index, device in self.list_input_devices():
            name = device["name"].lower()

            if card_token and card_token in name:
                if dev_token is None or f",{dev_token})" in name or f":{dev_token}" in name:
                    matches.append((index, device))
                    continue

            if query and query in name:
                matches.append((index, device))

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            self.logger.error(
                "Configured audio device '%s' matched multiple PortAudio inputs",
                configured_device,
            )
            for index, device in matches:
                self.logger.error("  [%s] %s", index, device["name"])
        return None

    def resolve_input_settings(self):
        try:
            device_info = sd.query_devices(AUDIO_DEVICE, "input")
            self.input_device = AUDIO_DEVICE
            self.input_device_name = str(AUDIO_DEVICE)
        except (sd.PortAudioError, ValueError):
            match = self.find_matching_input_device(AUDIO_DEVICE)
            if match is None:
                self.logger.error(
                    "Audio input device '%s' could not be resolved by PortAudio",
                    AUDIO_DEVICE,
                )
                self.log_available_input_devices()
                raise SystemExit(1) from None

            self.input_device, device_info = match
            self.input_device_name = device_info["name"]
            self.logger.info(
                "Resolved configured device '%s' to PortAudio input [%s] %s",
                AUDIO_DEVICE,
                self.input_device,
                self.input_device_name,
            )
        except Exception as exc:
            self.logger.error("Audio input device '%s' is unavailable: %s", AUDIO_DEVICE, exc)
            raise SystemExit(1) from exc

        self.sample_rate = (
            GROOVENET_SAMPLE_RATE
            if GROOVENET_INGEST_ENABLED
            else int(device_info["default_samplerate"]) or 44100
        )
        max_input_channels = int(device_info["max_input_channels"])
        if max_input_channels < CHANNELS:
            self.logger.error(
                "Input device '%s' only provides %s input channel(s), need %s",
                self.input_device_name or AUDIO_DEVICE,
                max_input_channels,
                CHANNELS,
            )
            raise SystemExit(1)
        self.logger.info(
            "Using audio input device '%s' at %s Hz with %s channels",
            self.input_device_name or AUDIO_DEVICE,
            self.sample_rate,
            CHANNELS,
        )
        return self.sample_rate

    def start_recording_writer(self):
        self.recording_writer = RecordingWriter(
            logger=self.logger,
            output_dir=RECORDINGS_DIR,
            channels=CHANNELS,
            sample_rate=self.sample_rate,
        )
        self.recording_writer.start()

    def start_groovenet_ingest(self):
        if not GROOVENET_INGEST_ENABLED:
            return
        self.groovenet_ingest = GroovenetIngest(
            sample_rate=self.sample_rate,
            channels=CHANNELS,
            logger=logging.getLogger("groovenet_ingest"),
        )
        self.groovenet_ingest.start()

    def run(self):
        sample_rate = self.resolve_input_settings()
        self.start_recording_writer()
        self.start_groovenet_ingest()

        self.client.connect_async(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
        self.client.loop_start()

        try:
            with sd.InputStream(
                device=self.input_device,
                samplerate=sample_rate,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=BLOCKSIZE,
                latency=STREAM_LATENCY,
            ) as stream:
                self.capture_thread = threading.Thread(
                    target=self.capture_loop,
                    args=(stream,),
                    name="audio-capture",
                    daemon=True,
                )
                self.capture_thread.start()

                self.logger.info(
                    "Monitoring RMS on %s threshold=%.6f blocksize=%s latency=%s"
                    " recordings_dir=%s attenuation_db=%.1f mode=blocking-read",
                    self.input_device_name or AUDIO_DEVICE,
                    RMS_THRESHOLD,
                    BLOCKSIZE,
                    STREAM_LATENCY,
                    RECORDINGS_DIR,
                    RECORDING_ATTENUATION_DB,
                )
                while not self.stop_event.is_set():
                    self.update_activity_state()
                    time.sleep(MAIN_LOOP_INTERVAL_SECONDS)
        except sd.PortAudioError as exc:
            self.logger.error("Failed to open audio stream on '%s': %s", AUDIO_DEVICE, exc)
            raise SystemExit(1) from exc
        finally:
            self.stop_event.set()
            if self.capture_thread is not None:
                self.capture_thread.join(timeout=5)
            if self.recording_writer is not None:
                self.recording_writer.set_recording(False)
                self.recording_writer.stop()
            if self.groovenet_ingest is not None:
                self.groovenet_ingest.stop()
            self.client.publish(RECORDING_STATE_TOPIC, "off", retain=True)
            self.client.disconnect()
            self.client.loop_stop()

    def shutdown(self, signum=None, frame=None):
        self.logger.info("Shutting down audio activity monitor")
        self.stop_event.set()


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main():
    configure_logging()
    monitor = AudioActivityMonitor()
    signal.signal(signal.SIGINT, monitor.shutdown)
    signal.signal(signal.SIGTERM, monitor.shutdown)

    try:
        monitor.run()
    except KeyboardInterrupt:
        monitor.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
