# Aswitch: Raspberry Pi Audio Source Switcher + 12V Amp Trigger

Aswitch is a Raspberry Pi-controlled audio switching and amplifier trigger system. It allows a Raspberry Pi streamer/DAC and an analog mixer to share a single amplifier input, while also controlling a 12V trigger output for powering the amplifier on/off.

## Core Features

- Switch between two stereo line-level sources:
  - Source A: Analog mixer / vinyl setup
  - Source B: Raspberry Pi USB DAC / streamer
- Route selected stereo source to amplifier RCA input
- Control an external amplifier via 12V trigger
- MQTT control and state publishing for Home Assistant integration
- Mixer/vinyl is the safe default source (relay de-energized)
- Pi kept physically separate from audio enclosure to reduce noise

## Files

### Services — `aswitch.local`

| File | Purpose |
|---|---|
| `aswitch.py` | GPIO relay control — switches audio source and 12V trigger via MQTT |
| `audio_activity.py` | USB audio RMS detector — publishes active/inactive state, optional WAV recording |
| `recording_archive.py` | Transfers completed WAVs to the Beelink archive and retains local copies for seven days after transfer |
| `ir_logger.py` | VS1838B IR receiver + IR LED blaster — receives raw frames and sends learned preamp commands via MQTT |
| `preamp_ir_codes.py` | Learned preamp IR fingerprint map used to tag known buttons |
| `preamp_trigger.py` | HY-M154 optocoupler monitor — publishes the preamp's physical 12V trigger state |
| `preamp_led.py` | TCS34725 monitor — publishes preamp LED color, inferred input state, and raw RGB readings |
| `deploy/aswitch.service` | systemd unit for relay control |
| `deploy/audio_activity.service` | systemd unit for audio activity detection |
| `deploy/ir_logger.service` | systemd unit for IR logging |

### Services — `pi-cam.local`

| File | Purpose |
|---|---|
| `dac_status.py` | USB DAC presence detector — polls `lsusb` and publishes MQTT state |
| `amp_trigger.py` | GPIO relay control — switches the amp 12V trigger via MQTT |
| `deploy/dac_status.service` | systemd unit for DAC presence detection |

`pi-cam.local` runs `dac_status.py` and `amp_trigger.py`. There is no audio-source switching or audio recording on that host.

### Deploy tooling

| File | Purpose |
|---|---|
| `deploy/deploy.sh` | rsync + venv + systemd restart over SSH |
| `deploy/push_env.sh` | push `.env` to a host and optionally restart services |
| `deploy/deploy_shairport_config.sh` | push tracked Shairport Sync config |

## Hardware Overview

### Main Components

- Raspberry Pi 3B
- USB DAC for streamer output
- Behringer UFO202 / USB Audio CODEC (audio activity detection)
- Electronics-Salon DPDT Signal Relay Module, 5VDC (RY5W-K) — stereo source switching
- 5V Tongling / JQC-3FF relay module — 12V trigger switching
- 12V DC power supply for amplifier trigger
- 6 RCA jacks: Mixer L/R in, DAC L/R in, Amplifier L/R out
- 3.5mm mono TS jack for amplifier trigger output

## Audio Signal Routing

The audio relay switches only the RCA center conductors. RCA grounds are never switched.

### Left Channel

```
Mixer L  -> NC1
DAC L    -> NO1
Output L <- COM1
```

### Right Channel

```
Mixer R  -> NC2
DAC R    -> NO2
Output R <- COM2
```

### Default Behavior

```
Relay de-energized: Mixer -> Amp
Relay energized:    DAC   -> Amp
```

### RCA Grounding

All RCA sleeve/ground connections tie to one shared audio ground bus:

```
Mixer RCA grounds ─┐
DAC RCA grounds   ─┼─> Shared audio ground bus
Output RCA grounds─┘
```

Rules:
- Do not switch RCA ground through the relay
- Do not use relay control ground as the audio return path
- Tie Pi/control ground to audio ground at one point only if required
- Use short, solid soldered or terminal connections

## GPIO Wiring

GPIO pins use BCM numbering. The audio-source relay remains on `aswitch.local` (`AUDIO_PIN = 17`); the amp trigger relay runs on `pi-cam.local` (`AMP_TRIGGER_GPIO = 27`).

> **Note:** Relay polarity may vary by module. In this build both relays behaved opposite to the initial active-low assumption. Active/inactive states are defined as constants in `aswitch.py`.

### Audio Relay — Electronics-Salon DPDT Board

| Pi Pin | Signal | Relay |
|---|---|---|
| Pin 2 / 5V | VCC | Relay VCC |
| Pin 6 / GND | GND | Relay GND |
| Pin 11 / GPIO17 | Control | Relay IN |

### Trigger Relay — 5V Tongling / JQC-3FF

| Pi Pin | Signal | Relay |
|---|---|---|
| Pin 2 / 5V | VCC | Relay DC+ |
| Pin 6 / GND | GND | Relay DC- |
| Pin 13 / GPIO27 | Control | Relay IN |

### Preamp Trigger Sense — HY-M154 / PC817 Optocoupler

The preamp's 3.5mm TS trigger output is read as an isolated GPIO input. Remove the
channel 1 jumper on the optocoupler board to keep the preamp and Pi grounds isolated.

| Connection | HY-M154 terminal |
|---|---|
| Preamp trigger TIP (+12V when on) | `IN1` |
| Preamp trigger SLEEVE | input-side `G` |
| Pi Pin 16 / GPIO23 | `V1` |
| Pi GND | output-side `G` |

The signal is active-low: `V1` is low when the preamp trigger is on.

### Preamp LED Monitor — TCS34725

Connect the sensor to the Pi I2C bus and mount it in a short, opaque shroud pointed at
the preamp's status LED to exclude room light. The service uses the trigger state as a
gate: if the preamp trigger is off, it reports the LED and input as `off`.

| Pi pin | TCS34725 connection |
|---|---|
| Pin 1 or 17 / 3.3V | VIN / 3V3 |
| Pin 6 / GND | GND |
| Pin 3 / GPIO2 | SDA |
| Pin 5 / GPIO3 | SCL |

Enable I2C on the Pi before deployment, then confirm the sensor is visible at `0x29`:

```sh
sudo raspi-config nonint do_i2c 0
i2cdetect -y 1
```

The default mapping is blue → `input_1` and red → `input_2`; adjust the environment
values after verifying the preamp's front-panel behavior. Raw readings are published so
the off threshold and color-dominance ratio can be calibrated without changing code.

## 12V Trigger Wiring

The trigger relay switches the positive leg of the 12V supply:

```
12V supply +  -> Relay COM
Relay NO      -> 3.5mm TS jack TIP
12V supply -  -> 3.5mm TS jack SLEEVE
```

Expected output:

```
Relay off -> 0V at trigger jack
Relay on  -> +12V at trigger jack
```

Verify with a multimeter (DC voltage, red to TIP, black to SLEEVE):

```
Amp off -> ~0V
Amp on  -> ~+12V
```

## MQTT Topics

### aswitch.py — Audio Source Control

| Topic | Direction | Payloads |
|---|---|---|
| `aswitch/audio` | command | `dac`, `mixer` |
| `aswitch/audio/state` | state (retained) | `dac`, `mixer` |
### amp_trigger.py — Amp Trigger Control (`pi-cam.local`)

| Topic | Direction | Payloads |
|---|---|---|
| `pi-cam/trigger` | command | `on`, `off` |
| `pi-cam/trigger/state` | state (retained) | `on`, `off` |

### audio_activity.py — Audio Monitor

| Topic | Direction | Payloads |
|---|---|---|
| `aswitch/audio_activity/state` | state (retained) | `active`, `inactive` |
| `aswitch/audio_activity/rms` | debug | RMS float string |
| `aswitch/audio_recording/set` | command | `on`, `off` |
| `aswitch/audio_recording/state` | state (retained) | `on`, `off` |
| `aswitch/audio_recording/file` | state (retained) | current WAV path |
| `aswitch/audio_recording/error` | event | error message string |
| `aswitch/audio_recording/archive` | state (retained) | JSON: `state`, `path`, `error` |

### dac_status.py — DAC Presence

| Topic | Direction | Payloads |
|---|---|---|
| `aswitch/dac/zd3/state` | state (retained) | `connected`, `disconnected` |
| `aswitch/dac/zd3/details` | state (retained) | matching `lsusb` line |
| `aswitch/dac/zd3/availability` | availability (retained) | `online`, `offline` |
| `aswitch/dac/zd3/command` | command | `start` |

### preamp_trigger.py — Preamp 12V Trigger Sense

| Topic | Direction | Payloads |
|---|---|---|
| `aswitch/preamp/trigger/state` | state (retained) | `on`, `off` |
| `aswitch/preamp/trigger/availability` | availability (retained) | `online`, `offline` |

### preamp_led.py — Preamp LED and Input Sense

| Topic | Direction | Payloads |
|---|---|---|
| `aswitch/preamp/led/state` | state (retained) | `red`, `blue`, `off`, `unknown` |
| `aswitch/preamp/input/state` | state (retained) | `input_1`, `input_2`, `off`, `unknown` |
| `aswitch/preamp/led/rgb` | debug (retained) | JSON object with `clear`, `red`, `green`, `blue` raw counts |
| `aswitch/preamp/led/availability` | availability (retained) | `online`, `offline` |

Publishing `start` to the command topic immediately triggers `systemctl start camilladsp.service`, bypassing the 30-second recovery timer. Intended for use from a Home Assistant automation when AirPlay playback begins — see [`home_assistant/camilladsp_trigger.yaml`](home_assistant/camilladsp_trigger.yaml).

### ir_logger.py — Raw IR Frame Logger

| Topic | Direction | Payloads |
|---|---|---|
| `aswitch/ir/received` | event | JSON object with `ts`, `gpio`, `tx_gpio`, `idle_gap_us`, `frame_type`, `pulses_us`, and for full frames `fingerprint_bits`, `fingerprint_hex`, optional `button` |
| `aswitch/ir/availability` | availability (retained) | `online`, `offline` |
| `aswitch/ir/send` | command | known button name such as `vol_up`, `vol_down`, `mute`, `input`, `light`, `power` |
| `aswitch/ir/sent` | event | JSON object with `ts`, `button`, `fingerprint_hex`, `tx_gpio`, `tx_frequency_hz`, `repeat_count` |

Known preamp button fingerprints:

| Button | Fingerprint bits | Hex |
|---|---|---|
| `vol_up` | `10110101000001000001010111101010` | `0xB50415EA` |
| `vol_down` | `10110101000001000101111110100000` | `0xB5045FA0` |
| `mute` | `10110101000001001010111101010000` | `0xB504AF50` |
| `input` | `10110101000001000011010111001010` | `0xB50435CA` |
| `light` | `10110101000001001101101100100100` | `0xB504DB24` |
| `power` | `10110101000001001001011101101000` | `0xB5049768` |

## Home Assistant Integration

Full MQTT config: [`home_assistant/mqtt_sensors.yaml`](home_assistant/mqtt_sensors.yaml)

```yaml
mqtt:
  switch:
    - name: "Amp Power"
      unique_id: aswitch_amp_power
      command_topic: "pi-cam/trigger"
      state_topic: "pi-cam/trigger/state"
      payload_on: "on"
      payload_off: "off"

    - name: "Audio Source DAC"
      unique_id: aswitch_audio_source_dac
      command_topic: "aswitch/audio"
      state_topic: "aswitch/audio/state"
      payload_on: "dac"
      payload_off: "mixer"

    - name: "Mixer Recording"
      unique_id: "aswitch_mixer_recording"
      command_topic: "aswitch/audio_recording/set"
      state_topic: "aswitch/audio_recording/state"
      payload_on: "on"
      payload_off: "off"

  binary_sensor:
    - name: "Mixer Audio Activity"
      unique_id: "aswitch_mixer_audio_activity"
      state_topic: "aswitch/audio_activity/state"
      payload_on: "active"
      payload_off: "inactive"
      device_class: sound

    - name: "Fosi ZD3 Status"
      unique_id: "fosi_zd3_status"
      state_topic: "aswitch/dac/zd3/state"
      payload_on: "connected"
      payload_off: "disconnected"
      availability_topic: "aswitch/dac/zd3/availability"
      payload_available: "online"
      payload_not_available: "offline"
      device_class: connectivity
      icon: "mdi:usb-port"
```

`Audio Source DAC ON` = DAC/streamer selected; `OFF` = mixer/vinyl selected.

`Fosi ZD3 Status` is driven by `dac_status.py` on `pi-cam.local` and is used by the ZD3 automation to know whether the amp is already on before sending an IR power command.

Tracked automations:

- [`home_assistant/amp_automation.yaml`](home_assistant/amp_automation.yaml) — amp auto-power from mixer audio activity
- [`home_assistant/zd3_auto_power.yaml`](home_assistant/zd3_auto_power.yaml) — ZD3 auto power/input from lounge streamer
- [`home_assistant/shairport_sync_example.yaml`](home_assistant/shairport_sync_example.yaml) — AirPlay trigger example
- [`home_assistant/camilladsp_trigger.yaml`](home_assistant/camilladsp_trigger.yaml) — proactively start CamillaDSP when AirPlay begins

## Audio Activity Detector

`audio_activity.py` listens to the Behringer UFO202 (`plughw:CARD=CODEC,DEV=0`), computes RMS across stereo input, and applies hold timers before publishing state changes.

The UFO202 identifies as:

```
08bb:2902 Texas Instruments PCM2902 Audio Codec
```

Useful ALSA commands:

```bash
arecord -l
arecord -L
arecord -D plughw:CARD=CODEC,DEV=0 -f cd -d 5 test.wav
```

### Activity Defaults

| Constant | Default | Description |
|---|---|---|
| `RMS_THRESHOLD` | `0.01` | RMS level that counts as audio |
| `ACTIVE_HOLD_SECONDS` | `2.0` | Sustained above threshold before publishing `active` |
| `INACTIVE_HOLD_SECONDS` | `300.0` | Sustained below threshold before publishing `inactive` |

Tune these on the Pi via `.env` if your mixer output level differs.

### Recording Defaults

Recordings preserve stereo, 16-bit PCM in uncompressed WAV files. With the
deployed GrooveNET configuration, the shared capture rate is 44.1 kHz. Recording
continues through silence until switched off. Open files use `.wav.part`; only
successfully closed files become `.wav` and can be archived.

The Ansible-managed archive timer copies completed WAVs to Beelink's Samba
archive using rsync over SSH, retries failures, and keeps each local original
for seven days after a successful transfer. Deploy with
`just deploy-aswitch-recordings` from the repository root. See
[the recording retrieval workflow](../../docs/aswitch.md#retrieving-vinyl-recordings)
for retrieval, configuration and MQTT sensor setup.

| Env var | Default | Description |
|---|---|---|
| `RECORDINGS_DIR` | `./recordings` | WAV output directory |
| `RECORDING_ATTENUATION_DB` | `0.0` | Gain applied before writing (e.g. `-6.0`) |
| `BLOCKSIZE` | `8192` | PortAudio block size |
| `STREAM_LATENCY` | `high` | PortAudio latency hint |

## DAC Status Publisher

`dac_status.py` polls `lsusb` on an interval and publishes retained MQTT state. Useful for triggering Home Assistant automations when a USB DAC is connected or disconnected.

Relevant `.env` keys:

```
DAC_USB_ID=152a:889b
DAC_MATCH_TEXT=Fosi Audio ZD3
DAC_POLL_INTERVAL_SECONDS=5
DAC_STATE_TOPIC=aswitch/dac/zd3/state
DAC_DETAILS_TOPIC=aswitch/dac/zd3/details
DAC_AVAILABILITY_TOPIC=aswitch/dac/zd3/availability
DAC_COMMAND_TOPIC=aswitch/dac/zd3/command
```

## Shairport Sync

Shairport Sync is used for AirPlay streaming to the USB DAC.

```bash
sudo systemctl restart shairport-sync
sudo systemctl status shairport-sync
journalctl -u shairport-sync -f
```

DAC discovery:

```bash
aplay -l
aplay -L
```

Deploy the tracked Shairport Sync config:

```bash
ASWITCH_HOST=pi-cam.local \
./deploy/deploy_shairport_config.sh
```

## Install Notes

System packages on Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y python3-venv python3-dev libportaudio2 portaudio19-dev libasound2-dev
```

Python packages:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Create the `.env` file on the Pi — copy from the template and fill in your credentials:

```bash
cp .env.example /home/YOUR_USER/aswitch/.env
$EDITOR /home/YOUR_USER/aswitch/.env
```

See [`.env.example`](.env.example) for all available variables.

## Deploy

The deploy script rsyncs Python files and `requirements.txt`, creates or reuses `.venv`, installs deps, and restarts the target service.

```bash
chmod +x deploy/deploy.sh
./deploy/deploy.sh                          # aswitch service -> aswitch.local
```

```bash
ASWITCH_SERVICE=audio_activity.service \
ASWITCH_SERVICE_TEMPLATE=audio_activity.service \
./deploy/deploy.sh                          # audio_activity service
```

```bash
ASWITCH_SERVICE=ir_logger.service \
ASWITCH_SERVICE_TEMPLATE=ir_logger.service \
./deploy/deploy.sh                          # ir_logger service
```

```bash
ASWITCH_HOST=pi-cam.local \
ASWITCH_SERVICE=dac_status.service \
ASWITCH_SERVICE_TEMPLATE=dac_status.service \
./deploy/deploy.sh                          # dac_status service -> pi-cam.local
```

Override any default:

```bash
ASWITCH_HOST=aswitch.local \
ASWITCH_USER=pi \
ASWITCH_REMOTE_DIR=/home/pi/aswitch \
ASWITCH_SERVICE=aswitch.service \
./deploy/deploy.sh
```

## Environment Management

Template files:

- [`.env.example`](.env.example) — shared MQTT and per-service defaults
- [`env/aswitch.example.env`](env/aswitch.example.env) — `aswitch.local` overrides
- [`env/pi-cam.example.env`](env/pi-cam.example.env) — `pi-cam.local` overrides

Create local secret files (gitignored):

```bash
cp env/aswitch.example.env env/aswitch.env
cp env/pi-cam.example.env env/pi-cam.env
```

Push env to a host:

```bash
ASWITCH_HOST=pi-cam.local \
ASWITCH_ENV_FILE=env/pi-cam.env \
./deploy/push_env.sh
```

```bash
ASWITCH_ENV_FILE=env/aswitch.env \
ASWITCH_RESTART_SERVICES="ir_logger.service" \
./deploy/push_env.sh
```

Push env and restart specific services:

```bash
ASWITCH_HOST=pi-cam.local \
ASWITCH_ENV_FILE=env/pi-cam.env \
ASWITCH_RESTART_SERVICES="dac_status.service,audio_activity.service" \
./deploy/push_env.sh
```

## Check Status On The Pi

```bash
ssh pi@aswitch.local
sudo systemctl status aswitch.service
sudo systemctl status audio_activity.service
journalctl -u ir_logger.service -f
journalctl -u aswitch.service -f
journalctl -u audio_activity.service -f
```

## Second Room — pi-cam.local (Fosi ZD3 + ESPHome IR)

The `pi-cam.local` host is a separate room audio system. It is architecturally distinct from `aswitch.local`: there is no relay switching or GPIO wiring. Instead, amp control goes over IR via an M5Stack Atom Echo running ESPHome.

### Components

- Raspberry Pi running `dac_status.py` (monitors USB DAC presence via `lsusb`)
- **Fosi ZD3 integrated amp** — controlled via IR remote
- **M5Stack Atom Echo** (ESP32) running ESPHome — emits NEC IR codes to the ZD3
- Shairport Sync for AirPlay streaming to the ZD3

### ESPHome IR Controller

Config: [`esphome/fosi-zd3-ir.yaml`](esphome/fosi-zd3-ir.yaml)

The Atom Echo emits NEC IR codes on GPIO12. All ZD3 IR commands use address `0x01FA`:

| Button | NEC Command |
|---|---|
| Power | `0xFA05` |
| Mute | `0xFD02` |
| Volume Up | `0xFC03` |
| Volume Down | `0xF807` |
| Display Toggle | `0xFE01` |
| USB | `0xEE11` |
| Optical | `0xF708` |
| Coax | `0xF609` |
| HDMI | `0xEC13` |
| Bluetooth | `0xEA15` |
| Play/Pause | `0xF50A` |
| Next | `0xF906` |
| Previous | `0xFB04` |

ESPHome secrets (Wi-Fi credentials, API key) go in `esphome/secrets.yaml` (gitignored). Copy from the template:

```bash
cp esphome/secrets.yaml.example esphome/secrets.yaml
$EDITOR esphome/secrets.yaml
```

> **Note on GPIO12:** The Atom Echo IR pin may vary by hardware revision. If GPIO12 does not transmit, use an external Grove IR emitter instead.

### Home Assistant Automations

Tracked automation: [`home_assistant/zd3_auto_power.yaml`](home_assistant/zd3_auto_power.yaml)

**ZD3: Auto power/input from lounge streamer** — driven by `media_player.lounge_streamer` state changes, with three branches:

| Trigger | TV state | ZD3 state | Action |
|---|---|---|---|
| Streamer → playing | any | off | Power on ZD3, switch to USB input, mute TV if on |
| Streamer → playing | any | on | Switch to USB input, mute TV if on |
| Streamer → idle/off (3 min) | on | on | Switch ZD3 to HDMI, unmute TV |
| Streamer → idle/off (3 min) | off | on | Switch ZD3 to HDMI, then power off ZD3 |

Key entities to replace for your setup:

| Placeholder | What it maps to |
|---|---|
| `media_player.lounge_streamer` | Your AirPlay / streamer media player entity |
| `media_player.lounge_tv` | Your TV media player entity |
| `remote.lounge_tv` | Your TV remote (used to read on/off state) |
| `binary_sensor.fosi_zd3_status` | DAC presence sensor from `dac_status.py` MQTT |
| `button.fosi_zd3_ir_zd3_*` | ESPHome IR buttons from `fosi-zd3-ir.yaml` |

The automation uses `mode: restart` so rapid streamer state changes retrigger cleanly rather than queuing.

### Pi-cam Deploy

`dac_status.py` runs on `pi-cam.local`. Deploy and push env the same way as other services:

```bash
ASWITCH_HOST=pi-cam.local \
ASWITCH_SERVICE=dac_status.service \
ASWITCH_SERVICE_TEMPLATE=dac_status.service \
./deploy/deploy.sh
```

```bash
ASWITCH_HOST=pi-cam.local \
ASWITCH_ENV_FILE=env/pi-cam.env \
./deploy/push_env.sh
```

## Enclosure Notes

Recommended final enclosure:
- Metal project enclosure for shielding and mechanical stability
- Pi kept in a separate enclosure
- Audio switcher box contains: RCA jacks, audio relay, trigger relay, 3.5mm trigger jack, ground bus

Prototype enclosure:
- 3D printed PETG or PLA
- Optional copper/aluminum tape shielding (connect to audio ground bus at one point)
- Use posts, tie-downs, and wire routing guides to keep RCA ground wiring short and solid

## PCB V2 Notes

A future PCB could replace hand wiring and include:

- 6 PCB or panel-mounted RCA jacks
- DPDT 5V signal relay for stereo source switching
- Trigger relay circuit
- 3.5mm trigger output
- GPIO/control header
- 5V/GND distribution
- Audio ground plane / bus
- Optional chassis ground point
- Optional on-board audio activity detector circuit

PCB traces are acceptable for line-level audio. Suggested layout rules:

- Short traces; solid ground plane
- Keep relay coil/control traces away from audio traces
- Switch only signal — never RCA ground
- Route left/right symmetrically where practical
- 20–30 mil traces for audio signals
