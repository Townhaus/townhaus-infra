# aswitch audio and power

`aswitch.local` receives AirPlay, switches sources through its GPIO relay, and
captures active vinyl playback for GrooveNET.

GrooveNET is routed through a static host entry for `groovenet.home.arpa`.
Cloud-init manages `/etc/hosts` on aswitch, so the Ansible deployment also
updates `/etc/cloud/templates/hosts.debian.tmpl` to retain the entry on reboot.

## Current signal path

```
AirPlay → Shairport Sync → Behringer UCA202 → TOSLINK → SMSL SU-1 → preamp
```

The SMSL is powered from its own USB supply. It is deliberately not connected
to the Pi over USB: the UCA202 is the only USB audio device on aswitch and
forwards Shairport playback digitally over TOSLINK. The UCA202 is limited to
16-bit, 44.1/48 kHz output, so this path is intended for AirPlay. Its analog
input remains the vinyl capture source.

CamillaDSP is installed but disabled on aswitch. To restore it, set
`camilladsp_enabled: true` in `ansible/group_vars/aswitch.yml` and run the full
aswitch deployment.

## Deploying changes

| Change | Command | What restarts |
|---|---|---|
| All aswitch services | `just deploy-aswitch` | Managed services as needed |
| AirPlay routing or level | `just deploy-aswitch-airplay` | `shairport-sync` |
| Vinyl ingest/client settings | `just deploy-aswitch-ingest` | `audio_activity` |
| Recording archive workflow | `just deploy-aswitch-recordings` | `audio_activity` when code changes; archive timer |
| Wi-Fi power saving | `just deploy-aswitch-wifi` | None; applies without reconnecting |

The Shairport output is pinned to 44.1 kHz, `S16_LE`, and a -12 dB maximum
software level. Interpolation uses Shairport's `auto` mode on aswitch. The
preamp remains the master volume.

The `pi_wifi` role disables power saving on the existing
`netplan-wlan0-GREATWHITE` NetworkManager connection and on the active `wlan0`
interface. Both the full deployment and `just deploy-aswitch-wifi` apply it
without reconnecting Wi-Fi. Verify the active setting with:

```bash
ssh aswitch.local '/usr/sbin/iw dev wlan0 get power_save'
```

The expected result is `Power save: off`.

## Retrieving vinyl recordings

Run `just deploy-aswitch-recordings` to install the archive workflow on the
existing aswitch setup. The full `just deploy-aswitch` deployment also includes
it. Deployment configures a dedicated SSH key on aswitch, authorizes its public
key on Beelink, and pins Beelink's SSH host key through the existing Ansible
connection. Both hosts need sudo access during deployment; routine transfers
run as the existing user without sudo.

Turn **Mixer Recording** on in Home Assistant to start a stereo, 16-bit,
44.1 kHz WAV with the configured -6 dB attenuation. Turn it off to finalize
the file. The recorder writes `.wav.part` while running and only renames it
to `.wav` after closing the WAV header successfully. Files left as `.part`
after a crash or write failure remain on the Pi for manual recovery and are
not transferred automatically.

`recording-archive.timer` checks for completed recordings about once a minute.
It uses rsync over SSH to copy them to
`/srv/storage/archive/recordings/vinyl/` on Beelink. Browse
`smb://beelink/archive` and open `recordings/vinyl` in Finder or Files. Names
include the recording start timestamp, such as
`audio-20261010-143000-123456.wav`; existing timestamped WAVs are also eligible.
After listening, rename the archived file with artist, album, and side if desired.

Interrupted transfers retain their temporary data in `.rsync-partial` on
Beelink and retry on later timer runs. The archive worker runs separately from
audio detection with reduced CPU and disk scheduling priority. Each successful
copy gets a durable local receipt in `recordings/.archive-state.json`; the Pi
keeps that original for seven days **from successful transfer**, then removes
it. Untransferred files are never removed by retention cleanup. Renaming the
archived copy does not trigger another upload.

Configure `recording_archive_retention_days`, `recording_archive_destination`,
or `recording_archive_host` in the aswitch group variables if needed. The
destination defaults to the configured Beelink Samba root plus
`/recordings/vinyl`. Keep the receipt file when managing recordings manually;
removing it makes remaining local WAVs eligible for transfer again.

The retained MQTT topic `aswitch/audio_recording/archive` contains JSON with
`state` (`transferring`, `ready`, or `error`), `path` (the Beelink destination),
and `error` (empty on success). The **Mixer Recording Archive** sensor in
`services/aswitch/home_assistant/mqtt_sensors.yaml` exposes the status and path
attributes. Merge that sensor into Home Assistant's existing MQTT configuration
and reload MQTT entities. The topic retains the latest transfer result between
timer runs; no new recording means no status change.

```bash
ssh aswitch.local 'systemctl list-timers recording-archive.timer --no-pager'
ssh aswitch.local 'journalctl -u recording-archive.service -n 50 --no-pager'
# Start a transfer scan immediately.
ssh -t aswitch.local 'sudo systemctl start recording-archive.service'
```

Local verification of recording finalization, retry behavior and retention:

```bash
PYTHONPATH=services/aswitch python3 -m unittest discover -s services/aswitch/tests -v
```

## Verify and troubleshoot

```bash
# Confirm the active Shairport output and playback errors.
ssh aswitch.local 'systemctl status shairport-sync --no-pager'
ssh aswitch.local 'journalctl -u shairport-sync -n 100 --no-pager'

# Inspect vinyl activity and GrooveNET upload attempts.
ssh aswitch.local 'journalctl -u audio_activity -n 100 --no-pager'

# Confirm the GrooveNET host entry survived reboot.
ssh aswitch.local 'getent hosts groovenet.home.arpa'

# Confirm the UCA202 is the only USB audio device.
ssh aswitch.local 'lsusb | grep -Ei "audio|Texas Instruments|SMSL"'
```

If GrooveNET uploads fail with `Name or service not known`, run
`just deploy-aswitch-ingest`. The playbook restores the current host entry and
its cloud-init template so the mapping survives future reboots.

## Power health

```bash
ssh aswitch.local 'vcgencmd get_throttled; vcgencmd measure_temp'
ssh aswitch.local 'journalctl -k -b --no-pager | grep -Ei "under-voltage|undervoltage|throttl"'
```

`throttled=0x0` after a reboot is the clean result. Historical bits remain set
until the next reboot; active fault bits indicate a current power problem.
