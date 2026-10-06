# pi-cam audio streaming

The configured playback path is:

```
AirPlay → Shairport Sync → USB → Fosi ZD3
```

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
ssh saegey@pi-cam.local 'systemctl is-active shairport-sync; systemctl is-active camilladsp camillagui camilladsp-recover.timer'
ssh saegey@pi-cam.local 'journalctl -u shairport-sync -n 100 --no-pager'
```

Shairport should be active and the DSP units inactive. Compare startup time,
track-change delay, and dropouts using the same AirPlay source. Direct output
removes DSP processing and buffering, but AirPlay protocol latency still applies.
