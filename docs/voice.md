# Home Assistant voice

Beelink runs two local speech services in the root Docker Compose stack:

| Service | Image release | LAN TCP port | Persistent model directory |
|---|---|---|---|
| `wyoming-whisper` | `rhasspy/wyoming-whisper:3.8.1` | 10300 | `/srv/storage/wyoming/whisper` |
| `wyoming-piper` | `rhasspy/wyoming-piper:2.5.3` | 10200 | `/srv/storage/wyoming/piper` |

Both image references in `docker-compose.yml` include immutable multi-platform
manifest digests, verified against Docker Hub on 2026-11-16. These are the
stable [Whisper 3.8.1](https://github.com/OHF-Voice/wyoming-faster-whisper/releases/tag/v3.8.1)
and [Piper 2.5.3](https://github.com/OHF-Voice/wyoming-piper/releases/tag/v2.5.3)
releases. Piper uses the standard Piper image, without the larger OmniVoice backend.

Wyoming uses raw TCP, with no Caddy route or TLS. The published ports listen on
the host's interfaces; keep them on trusted networks and do not forward them
through the internet router. Home Assistant at `192.168.2.91` must be able to
reach Beelink on both ports.

## Models and resource limits

Whisper uses faster-whisper `small-int8`, English (`--language en`), CPU int8
inference, beam size 1, and four inference threads. This starts with a balance
of recognition accuracy and CPU cost on the i3-1220P; beam size 1 reduces
decoding work. Its hard memory limit is 2 GiB. If recognition takes too long or
the container runs out of memory, change `--model small-int8` to
`--model base-int8` in `docker-compose.yml` and redeploy. The smaller model is
the faster fallback, with a possible accuracy tradeoff. Latency and peak memory
still need verification on Beelink alongside Frigate and Immich.

Piper uses `en_US-lessac-medium`, a natural-sounding US English voice with a
modest CPU and memory footprint. Its hard memory limit is 512 MiB. To change
the voice, edit `--voice` in `docker-compose.yml` using a voice from the
[Piper voice catalog](https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US)
and redeploy. The selected voice downloads on first use/startup.

Both services mount their host model directory at `/data`, retain downloads
across container recreation, restart unless stopped, and rotate container logs.
Memory limits bound these services' RAM usage; they do not reserve RAM or CPU
for the rest of the stack.

## Deploy and monitor

From the repo checkout on the control machine:

```bash
just deploy-stack
just configure-uptime-kuma
```

`deploy-stack` creates the model directories, copies the root Compose file,
pulls images, and starts the stack. It uses the stack's existing inventory and
1Password prerequisites; voice needs no new secrets or environment variables.
It also reconciles the other services in the root stack. No separate voice
playbook or host-role deployment is needed.

The Kuma role appends **Wyoming Whisper** and **Wyoming Piper** TCP monitors to
the existing inventory monitors, using Docker service names on the shared
network. They test TCP availability, as does each container's Compose
healthcheck (the images define none of their own). Existing local vars need no edits.
Optionally add those names to the local status page's Beelink `monitorList`
to display them there; the tracked vars example shows this. To customize or
disable the appended monitors, override `uptime_kuma_voice_monitors` in your
local vars (`[]` disables reconciliation; use `uptime_kuma_monitors_absent`
to remove monitors already created).

## Connect Home Assistant

On the separate Home Assistant host, open **Settings → Devices & services →
Add integration → Wyoming Protocol**. Add each service separately:

| Service | Host | Port |
|---|---|---|
| Whisper | `beelink.local` (or Beelink's LAN IP, currently `192.168.2.151`) | 10300 |
| Piper | `beelink.local` (or the same LAN IP) | 10200 |

Use the LAN IP if mDNS resolution fails. Select Whisper for speech-to-text and
Piper for text-to-speech in the desired Assist pipeline under **Settings →
Voice assistants**. Home Assistant configuration is a manual step; this repo
does not change that host.

## Verify and troubleshoot

After deploying, test the published listeners from a LAN client:

```bash
nc -vz beelink.local 10300
nc -vz beelink.local 10200
just logs wyoming-whisper
just logs wyoming-piper
```

On Beelink, inspect container status, logs, healthcheck output and memory use:

```bash
cd /srv/docker/townhaus-caddy
docker compose ps wyoming-whisper wyoming-piper
docker logs --tail=100 wyoming-whisper
docker logs --tail=100 wyoming-piper
docker inspect --format '{{json .State.Health}}' wyoming-whisper
docker inspect --format '{{json .State.Health}}' wyoming-piper
docker inspect --format 'OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}}' wyoming-whisper
docker inspect --format 'OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}}' wyoming-piper
docker stats --no-stream wyoming-whisper wyoming-piper
```

First startup needs internet access and disk space to download the model and
voice. The listeners may not open until downloads and model loading finish.
Each service's Compose healthcheck opens its Wyoming port every 30 seconds,
with a 10-second timeout, three retries and a five-minute startup grace period. A slow
first download can outlast that grace; watch the logs before diagnosing a
failure. Docker health status alone does not restart an unhealthy container.

If startup fails, check logs for download/DNS errors, full storage, or `/data`
permission errors. Verify both `.onnx` and `.onnx.json` files exist for Piper's
voice after an interrupted download. For OOM kills or slow Whisper responses,
try `base-int8` before increasing limits on this shared host. For refused or
timed-out LAN connections, check container health, the host/IP, port conflicts,
and firewall access from Home Assistant. TCP success only proves connectivity;
finish with a spoken Assist request to verify transcription and synthesis.
