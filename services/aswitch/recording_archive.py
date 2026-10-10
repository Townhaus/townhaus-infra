"""Archive finalized recordings over SSH; retain local copies after success."""

import json
import logging
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

import paho.mqtt.client as mqtt

TOPIC = "aswitch/audio_recording/archive"
RECORDING_NAME = re.compile(r"^audio-\d{8}-\d{6}(?:-\d{6})?\.wav$")


class RecordingArchive:
    def __init__(
        self, output_dir, destination, transfer, publish, retention_days=7, target_id=None
    ):
        self.output_dir = Path(output_dir)
        self.destination = destination
        self.target_id = target_id or destination
        self.transfer = transfer
        self.publish = publish
        self.retention_seconds = float(retention_days) * 86400
        if self.retention_seconds <= 0:
            raise ValueError("Recording retention must be greater than zero")
        self.state_path = self.output_dir / ".archive-state.json"
        self.logger = logging.getLogger("recording_archive")

    def _save(self, state):
        temporary = self.state_path.with_suffix(".tmp")
        with temporary.open("w") as handle:
            json.dump(state, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.state_path)

    def run(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Fail closed if receipts are damaged: never infer a successful transfer.
        state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        failed = False
        for path in sorted(self.output_dir.glob("audio-*.wav")):
            if not RECORDING_NAME.fullmatch(path.name) or path.is_symlink():
                continue
            stat = path.stat()
            signature = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            receipt = state.get(path.name)
            if (
                receipt
                and receipt["signature"] == signature
                and receipt.get("target") == self.target_id
            ):
                if time.time() - receipt["archived_at"] >= self.retention_seconds:
                    path.unlink()
                    del state[path.name]
                    self._save(state)
                    self.logger.info("Removed archived local recording: %s", path.name)
                continue

            archive_path = f"{self.destination.rstrip('/')}/{path.name}"
            self.publish("transferring", archive_path, "")
            try:
                self.transfer(path)
                # A completed file must stay immutable throughout transfer.
                after = path.stat()
                if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                    raise RuntimeError("Recording changed during transfer")
                state[path.name] = {
                    "signature": signature,
                    "archived_at": time.time(),
                    "target": self.target_id,
                }
                self._save(state)
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                failed = True
                self.logger.exception("Recording archive failed for %s", path.name)
                self.publish("error", archive_path, str(exc))
                # Avoid repeated connection timeouts for every file during an outage.
                break
            self.logger.info("Recording archived: %s", archive_path)
            self.publish("ready", archive_path, "")
        return not failed


def make_transfer(host, user, destination, identity, known_hosts):
    # These values also cross the remote shell boundary used by rsync.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host):
        raise ValueError("Invalid archive SSH hostname")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", user):
        raise ValueError("Invalid archive SSH user")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", destination):
        raise ValueError("Archive destination must be an absolute path without spaces")
    ssh = shlex.join(
        [
            "ssh",
            "-i",
            str(identity),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
        ]
    )

    def transfer(path):
        result = subprocess.run(
            [
                "rsync",
                "-tp",
                "--checksum",
                "--chmod=F660",
                "--partial-dir=.rsync-partial",
                "--timeout=120",
                "-e",
                ssh,
                "--",
                str(path),
                f"{user}@{host}:{destination.rstrip('/')}/",
            ],
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"rsync exited {result.returncode}: {result.stderr.strip()}")

    return transfer


def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    recordings = Path(os.environ.get("RECORDINGS_DIR", "./recordings"))
    destination = os.environ["RECORDING_ARCHIVE_DESTINATION"]
    host = os.environ["RECORDING_ARCHIVE_HOST"]
    transfer = make_transfer(
        host,
        os.environ["RECORDING_ARCHIVE_USER"],
        destination,
        os.environ["RECORDING_ARCHIVE_IDENTITY"],
        os.environ["RECORDING_ARCHIVE_KNOWN_HOSTS"],
    )
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if os.environ.get("MQTT_USER"):
        client.username_pw_set(os.environ["MQTT_USER"], os.environ.get("MQTT_PASS"))
    mqtt_connected = False
    try:
        client.connect(
            os.environ.get("MQTT_HOST", "homeassistant.local"),
            int(os.environ.get("MQTT_PORT", "1883")),
            60,
        )
        client.loop_start()
        mqtt_connected = True
    except OSError:
        logging.getLogger("recording_archive").exception("Archive MQTT connection failed")

    def publish(status, path, error):
        if not mqtt_connected:
            return
        # One retained payload keeps status, destination and error consistent.
        info = client.publish(
            TOPIC,
            json.dumps(
                {
                    "state": status,
                    "path": path,
                    "error": error,
                }
            ),
            qos=1,
            retain=True,
        )
        try:
            info.wait_for_publish(timeout=5)
        except RuntimeError:
            logging.getLogger("recording_archive").warning("Archive MQTT publish failed")

    try:
        success = RecordingArchive(
            recordings,
            destination,
            transfer,
            publish,
            os.environ.get("RECORDING_ARCHIVE_RETENTION_DAYS", "7"),
            target_id=f"{host}:{destination}",
        ).run()
    except (OSError, ValueError, KeyError, TypeError):
        logging.getLogger("recording_archive").exception("Recording archive scan failed")
        publish("error", "", "Archive scan failed; see recording-archive.service logs")
        success = False
    finally:
        if mqtt_connected:
            client.disconnect()
            client.loop_stop()
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
