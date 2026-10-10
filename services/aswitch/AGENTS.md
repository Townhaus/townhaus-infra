# AGENTS.md — aswitch

Raspberry Pi MQTT relay controller and audio monitoring suite. Independent Python services run on one or more Pis and publish state to Home Assistant via MQTT.

## Services

### `aswitch.local`

| File | Systemd unit | Purpose |
|---|---|---|
| `aswitch.py` | `aswitch.service` | GPIO relay switch — routes audio source (DAC vs mixer) and controls a trigger output via MQTT commands |
| `audio_activity.py` | `audio_activity.service` | USB audio RMS detector — publishes activity and, when active, sends 15-second vinyl windows to Groovenet |
| `recording_archive.py` | `recording-archive.service` / `.timer` | Archives completed WAVs to Beelink over SSH and cleans local originals seven days after success |
| `ir_logger.py` | `ir_logger.service` | VS1838B IR receiver + blaster — publishes received raw frames and sends learned preamp commands via MQTT |
| `preamp_trigger.py` | `preamp_trigger.service` | HY-M154 optocoupler monitor — publishes the preamp's physical 12V trigger state |
| `preamp_led.py` | `preamp_led.service` | TCS34725 monitor — publishes preamp LED color, input state, and raw RGB readings |
| `preamp_ir_codes.py` | n/a | Learned fingerprint map for recognized preamp remote buttons |

### `pi-cam.local`

| File | Systemd unit | Purpose |
|---|---|---|
| `dac_status.py` | `dac_status.service` | USB DAC presence detector — polls `lsusb` and publishes connected/disconnected state |
| `amp_trigger.py` | `amp_trigger.service` | GPIO relay switch — controls the amp 12V trigger via MQTT |

`pi-cam.local` runs the DAC status monitor and amp trigger relay. It has no audio-source switching or audio recording.

## Key patterns

**Configuration** — All tunables come from environment variables (loaded from `.env` on the Pi via the systemd `EnvironmentFile=` directive). No config files, no argparse. Module-level constants are derived from `os.environ.get(...)` at import time.

**MQTT** — All three services use `paho-mqtt` v2 callback API (`CallbackAPIVersion.VERSION2`). Guard credential calls with `if MQTT_USERNAME` — do not call `username_pw_set()` unconditionally, as passing `None` can cause connection failures.

**Logging** — Use `logging` throughout; never `print()`. The root logger is configured in `main()` via `logging.basicConfig`. Pass format args to the logger call (`logger.info("x=%s", x)`) rather than f-strings, so the string is only rendered when the message is actually emitted.

**Threading in `audio_activity.py`** — `RecordingWriter` is a daemon `Thread`. Shared mutable state (`recording_enabled`, `current_wave`, `current_path`, `dropped_blocks`, `last_error`) is protected by `self.state_lock`. Anything you read or write from outside the `RecordingWriter` thread must hold that lock.

**Groovenet ingest** — `audio_activity.py` feeds the same capture stream to
`groovenet_ingest.py`, so do not create a second process that opens the USB
audio device. It writes completed windows atomically to `GROOVENET_SPOOL_DIR`.
The filename is the durable ingest metadata: timestamp, session UUID, and
sequence. Preserve it when changing the uploader; those fields form the
server's idempotency key. On aswitch it is gated by the RMS activity state;
discard a partial window when entering an intentional inactive period rather
than joining audio from separate playback sessions.

**Recording archives** — Open WAVs must use `.wav.part`; promote to `.wav`
only after successful close. The archive worker runs separately and selects
only completed timestamped WAVs. Retention starts at successful transfer and
requires a matching durable receipt in `.archive-state.json`; never delete an
untransferred recording or select `.part` files. Preserve receipts when renaming
archived recordings. Deployment lives in `ansible/roles/recording_archive`.

**`logger.exception()`** — automatically appends the current exception's traceback and message. Do not pass the caught exception as a format argument — `logger.exception("Failed")` is correct; `logger.exception("Failed: %s", exc)` is redundant.

## Linting

```bash
pip install ruff
ruff check .        # lint
ruff check . --fix  # auto-fix safe issues
ruff format .       # format
```

Config lives in `pyproject.toml`. Rules enabled: `E`, `W`, `F` (pyflakes), `I` (isort), `UP` (pyupgrade), `B` (bugbear), `LOG`/`G` (logging correctness).

## Deploy

```bash
./deploy/deploy.sh                                     # aswitch service to aswitch.local
ASWITCH_SERVICE=audio_activity.service \
ASWITCH_SERVICE_TEMPLATE=audio_activity.service \
./deploy/deploy.sh                                     # audio_activity service
ASWITCH_SERVICE=ir_logger.service \
ASWITCH_SERVICE_TEMPLATE=ir_logger.service \
./deploy/deploy.sh                                     # ir_logger service

ASWITCH_HOST=pi-cam.local \
ASWITCH_SERVICE=dac_status.service \
ASWITCH_SERVICE_TEMPLATE=dac_status.service \
./deploy/deploy.sh                                     # dac_status service to pi-cam.local
```

The deploy script rsyncs Python files and `requirements.txt`, creates or reuses a virtualenv at `/home/<user>/aswitch/.venv`, installs deps, and restarts the service.

Push updated `.env` without redeploying code:

```bash
ASWITCH_ENV_FILE=env/aswitch.env \
ASWITCH_RESTART_SERVICES=ir_logger.service \
./deploy/push_env.sh

ASWITCH_HOST=pi-cam.local \
ASWITCH_ENV_FILE=env/pi-cam.env \
./deploy/push_env.sh
```

## Environment files

| File | Purpose |
|---|---|
| `.env.example` | Template for a single-host `.env` |
| `env/aswitch.example.env` | Template for `aswitch.local` |
| `env/pi-cam.example.env` | Template for `pi-cam.local` |

Secret `.env` files (gitignored): `env/aswitch.env`, `env/pi-cam.env`, and any file matching `env/*.env` that does not end in `.example.env`.

## Testing

Recording finalization, transfers and retention have automated tests:

```bash
PYTHONPATH=services/aswitch python3 -m unittest discover -s services/aswitch/tests -v
```

The remaining services are hardware-coupled (GPIO, USB audio, `lsusb`). Validate changes by:

1. Running `ruff check .` locally.
2. Deploying to the Pi and tailing the journal: `journalctl -u <service>.service -f`.

## Home Assistant integration

MQTT topics published by each service are documented in `README.md`. Example sensor/switch YAML is in `home_assistant/`. The `amp_automation.yaml` automation auto-powers the amp based on audio activity.
