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

The Shairport output is pinned to 44.1 kHz, `S16_LE`, and a -12 dB maximum
software level. Interpolation uses Shairport's `auto` mode on aswitch. The
preamp remains the master volume.

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
