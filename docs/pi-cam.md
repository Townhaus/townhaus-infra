# pi-cam audio streaming

## Vinyl capture

```
Vinyl player -> Yamaha mixer -> Scarlett 6i6 analogue inputs 3/4
                             -> pi-cam audio_activity
                                -> MQTT activity and recording controls
                                -> GrooveNET (source_id: pi-cam)
                                -> finalized WAVs -> Beelink archive
```

Run `just deploy-pi-cam-capture` to configure capture and archiving using the
existing MQTT credentials, without changing playback or GPIO services. Run
`just deploy-audio-capture` to refresh both Pi capture installations. Full
`just deploy-pi-cam` deployments also include capture and archiving.

The Scarlett's native stream has six channels. `AUDIO_CAPTURE_CHANNELS=6` and
`AUDIO_CHANNEL_MAP=3,4` select the mixer feed once, before RMS detection,
recording, and GrooveNET ingest. The default two-channel ALSA capture reads
inputs 1/2 and misses this feed. The capture device is matched by its Scarlett
name instead of its numeric card index.

Settings match aswitch: 44.1 kHz stereo, 16-bit WAV, -6 dB recording attenuation,
15-second GrooveNET windows gated by activity, RMS threshold 0.01, two seconds
to become active, and 30 seconds to become inactive. The -6 dB attenuation is
applied to saved recordings; adjust the mixer output to provide ADC headroom.

`AUDIO_MQTT_TOPIC_PREFIX=pi-cam` keeps recording commands, states, RMS, archive
status, and the MQTT client identity separate from aswitch. The Home Assistant
examples in `services/aswitch/home_assistant/mqtt_sensors.yaml` include
**Lounge Vinyl Recording**, **Lounge Vinyl Audio Activity**, and
**Lounge Vinyl Recording Archive**. Merge them into the existing MQTT config
and reload MQTT entities.

Completed recordings appear at
`smb://beelink/archive/recordings/vinyl/pi-cam/`. Open recordings remain
`.wav.part`; the timer transfers only finalized WAVs and keeps local originals
for seven days after successful transfer, just like aswitch.

Verify with:

```bash
ssh saegey@pi-cam.local 'systemctl is-active audio_activity recording-archive.timer'
ssh saegey@pi-cam.local 'journalctl -u audio_activity -n 80 --no-pager'
```

Optional [live streaming](vinyl-live.md) uses the same selected stereo blocks
through a bounded queue and separate network worker. Beelink encodes two
independent MP3 listener URLs, with source and public-tunnel switches in
Home Assistant. Live forwarding starts off and never opens a second capture
stream or blocks detection/recording on network I/O.

## AirPlay playback

The configured playback path is:

```
AirPlay → Shairport Sync → USB → Fosi ZD3
```

The AirPlay 2 build's MPRIS metadata bridge publishes track details to the
existing `pi-cam/shairport/{title,artist,album,genre,cover}` MQTT topics for the
Home Assistant Shairport integration. Cover payloads are binary image data.
The native Shairport MQTT client continues to publish playback events and
status. The bridge is optional and can be disabled with
`shairport_mpris_mqtt_bridge_enabled: false` in
`ansible/group_vars/pi_cam.yml`.

Shairport uses `auto` interpolation and sends 44.1 kHz, `S32_LE` audio directly
to `plughw:CARD=ZD3,DEV=0`. The ZD3 controls volume.

CamillaDSP is disabled, so its EQ presets do not affect playback. The deployment
stops and disables DSP, its GUI, and its recovery timer; removes the DAC
hot-plug startup rule; and disconnects the DAC monitor from MQTT commands that
start DSP. Existing DSP binaries and presets remain available.

Apply with `just deploy-pi-cam`. This restarts affected audio services and may
interrupt playback. Power on the ZD3 before testing audio.

To restore DSP, set `camilladsp_enabled: true` in
`ansible/group_vars/pi_cam.yml` and deploy again. The loopback routing,
Shairport dependency, DAC hot-plug rule, recovery timer, and MQTT command
subscription return automatically.

Verify after deployment:

```bash
ssh saegey@pi-cam.local 'systemctl is-active shairport-sync-airplay2 shairport-mpris-mqtt; systemctl is-active shairport-sync.service camilladsp camillagui camilladsp-recover.timer'
ssh saegey@pi-cam.local 'journalctl -u shairport-sync-airplay2 -u shairport-mpris-mqtt -n 100 --no-pager'
```

The AirPlay 2 receiver and metadata bridge should be active; the classic
receiver and DSP units should be inactive. After deployment, verify:

- Safari playback from the GrooveNET web app's audio element to **Lounge Room Audio**.
- macOS system-audio AirPlay and iPhone AirPlay to the receiver.
- Home Assistant receives title, artist, album, and cover art after track changes.
- Native `playing` and play-event MQTT topics continue to update.
- Sound quality against the classic build using a familiar record. To switch
  back, set `shairport_airplay2_enabled: false` in
  `ansible/group_vars/pi_cam.yml` and run the same Shairport playbook.

Direct output removes DSP processing and buffering, but AirPlay protocol latency
still applies.
