# On-demand vinyl live streams

Both Pis use their existing `audio_activity` capture stream. A bounded queue
forwards the selected 44.1 kHz, stereo, 16-bit PCM to Beelink only when enabled.
Beelink starts an FFmpeg MP3 encoder for each enabled, connected source and
serves independent listener URLs:

- Local lounge: `http://192.168.2.151:8098/pi-cam.mp3`
- Local aswitch: `http://192.168.2.151:8098/aswitch.mp3`
- Public lounge: `https://<your-tunnel-hostname>/pi-cam.mp3`
- Public aswitch: `https://<your-tunnel-hostname>/aswitch.mp3`

MP3 output is 192 kbps stereo. The private LAN feed is lossless PCM; the
recording-only -6 dB attenuation does not change the live feed. Detection,
GrooveNET, and WAV recording keep running when live streaming is off. Streams
include silence until explicitly disabled; RMS activity does not gate them.

## Home Assistant controls

The Beelink controller publishes MQTT discovery for a **Vinyl Live** device
with three switches and two source-status sensors. Existing aswitch and
pi-cam recording/activity entities are separate. MQTT discovery must be
enabled in Home Assistant. The tunnel switch is unavailable until its token
is configured; source controls require their Pi forwarder to be online.

| Control | Command topic | Retained state topic |
|---|---|---|
| Lounge Live Stream | `vinyl-live/pi-cam/set` | `vinyl-live/pi-cam/state` |
| Aswitch Live Stream | `vinyl-live/aswitch/set` | `vinyl-live/aswitch/state` |
| Public Tunnel | `vinyl-live/tunnel/set` | `vinyl-live/tunnel/state` |

Commands and switch states use `on` / `off`. Commands must **not** be retained.
The controller ignores retained commands so an old `on` cannot restart a
broadcast after a reboot. Both sources can be enabled together. A source
switch means forwarding is enabled; its status sensor reports `waiting`,
`streaming`, or `off` on `vinyl-live/<source>/status`.

`vinyl-live/availability` is `online` while the controller is connected and
uses a retained MQTT last will of `offline`. The tunnel switch also checks
`vinyl-live/tunnel/availability`, which reports whether a token is configured.
`vinyl-live/error` reports rejected requests such as enabling an unconfigured
tunnel. Pi transport status is published at `<source>/audio_live/status`.

Switching off a source closes its encoder and listeners without stopping the
other source. Switching off **Public Tunnel** stops external access while
local streams continue. The tunnel's switch state becomes `on` only after
cloudflared's readiness endpoint confirms a connection.

All controls start **off** after a controller restart or host reboot. MQTT
disconnects also disable forwarding and the public tunnel; re-enable the
desired sources after connectivity returns. A restarting Pi can rejoin a
source that is still enabled on Beelink.

## Deployment

Set the optional token reference in
`ansible/group_vars/townhaus_caddy/vinyl_live.yml`:

```yaml
vinyl_live_tunnel_token_ref: op://Homelab/Cloudflare Vinyl Tunnel/token
```

Only the reference belongs in Git. The deployment retrieves the token with
`op read`, writes a mode-0600 file on Beelink, and mounts it read-only into
the tunnel container. It never places the value in the command line, build
context, or MQTT. The LAN source credentials are generated separately on
Beelink, persist across redeployments, and are installed in mode-0600
`.live-audio.env` files on the Pis. MQTT credentials use the existing
1Password references.

An empty tunnel reference installs LAN streaming and leaves public access
unavailable. Configure the Cloudflare tunnel's public hostname to use this
HTTP origin:

```text
http://server:8081
```

The connector shares the streaming Compose network, where `server` resolves
to the listener service. Only the listener HTTP port is routed through the
tunnel. PCM ingest is a separate authenticated TCP port (`8097`) on the LAN.
The public listener has only the two MP3 routes and a health endpoint; it
has no control API. Public URLs are accessible to anyone who can reach the
configured hostname unless access restrictions are configured separately.

Run:

```bash
just deploy-vinyl-live
```

This deploys Beelink and both Pi forwarders together, then restarts capture
with live forwarding off. It refuses to restart a Pi capture process that
currently has an open recording. Beelink deployment runs as its existing
Docker-capable SSH account; the Pis require sudo for their capture units.
Only open WAVs inside the recording output directory block a restart;
GrooveNET's temporary analysis windows do not.

Lightweight control processes remain running in two containers. FFmpeg
processes and the actual Cloudflare connector run only on demand. Neither
container has the Docker socket. The tunnel supervisor requires a renewed
five-second lease from the controller and rejects leases older than its own
startup; a dead controller cannot leave the tunnel running indefinitely.

## Verification

```bash
ssh beelink 'cd /srv/docker/vinyl-live && docker compose -p vinyl-live ps'
curl http://192.168.2.151:8098/healthz
ssh saegey@pi-cam.local 'journalctl -u audio_activity -n 30 --no-pager'
ssh saegey@aswitch.local 'journalctl -u audio_activity -n 30 --no-pager'
```

An off or disconnected source returns HTTP 503. A connected source returns
`audio/mpeg` with caching disabled. Slow listeners are disconnected rather
than stalling the encoder; slow LAN sends never block capture, and old queued
audio is discarded during disconnect/reconnect. Each source allows up to
20 simultaneous listeners.

Tests:

```bash
python -m pip install -r services/aswitch/tests/requirements.txt
PYTHONPATH=services/aswitch python -m unittest discover -s services/aswitch/tests -v
docker build -t vinyl-live:test services/vinyl-live
docker run --rm --network none \
  -v "$PWD/services/vinyl-live/tests:/tests:ro" \
  vinyl-live:test python -m unittest discover -s /tests -v
```
