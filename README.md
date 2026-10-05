# townhaus-caddy

Homelab monorepo managing a Caddy reverse proxy stack, Frigate NVR, Raspberry Pi audio services, CamillaDSP, Beszel monitoring, and AdGuard DNS — all deployed via Ansible.

For a terminal workspace with repo, Caddy, stack operations, and audio tabs,
see [Townhaus Infra in Herdr](docs/herdr.md).

## Hosts

| Host | Role |
|---|---|
| `beelink.local` | Main server — Caddy, AdGuard, Immich, Beszel Hub |
| `aswitch.local` | Audio Pi — Shairport, GPIO relay, and vinyl-ingest services |
| `pi-cam.local` | Lounge Pi — Shairport, CamillaDSP, DAC status, amp-trigger relay |

## Services

### beelink.local (Docker Compose)

| Service | URL | Description |
|---|---|---|
| Caddy | — | Reverse proxy with internal TLS CA |
| AdGuard Home | `https://adguard` | DNS server and ad blocker |
| Immich | `https://immich` | Photo library |
| Frigate | `https://frigate` | NVR / camera |
| Groovenet | `https://groovenet` | Music streaming |
| Home Assistant | `https://ha` | Home automation |
| Scrypted | `https://scrypted` | Camera management |
| UniFi | `https://unifi` | Network management |
| Beszel | `https://beszel` | Node monitoring |
| Uptime Kuma | `https://uptime` | HTTP endpoint monitoring |
| Grafana | `https://grafana` | Central log exploration and dashboards |
| PVR | `https://pvr` | Website-change monitor |

### aswitch.local (systemd)

| Service | Description |
|---|---|
| `aswitch.service` | GPIO relay — routes audio source via MQTT |
| `audio_activity.service` | USB audio RMS detector — publishes active/inactive state |
| `groovenet_ingest` (within `audio_activity.service`) | Activity-gated 15-second vinyl capture and Groovenet upload |
| `camilladsp.service` / `camillagui.service` | Installed but disabled; can be restored with `camilladsp_enabled: true` |
| `shairport-sync.service` | AirPlay receiver |

### pi-cam.local (systemd)

| Service | Description |
|---|---|
| `dac_status.service` | USB DAC presence detector — publishes to MQTT |
| `amp_trigger.service` | GPIO relay — controls the amp 12V trigger via MQTT |
| `camilladsp.service` | DSP engine — EQ and processing |
| `camillagui.service` | CamillaGUI web UI (`https://pi-cam`) |
| `shairport-sync.service` | AirPlay receiver → ALSA Loopback → CamillaDSP |

### Audio signal chains

```
aswitch: AirPlay → Shairport Sync → UCA202 → TOSLINK → externally powered SMSL SU-1 → preamp
pi-cam:  AirPlay → Shairport Sync → ALSA Loopback → CamillaDSP → USB DAC → speakers
```

## Vinyl capture to Groovenet

`audio_activity.service` captures the turntable chain and uploads contiguous
15-second mono, 16-bit WAV windows to Groovenet only while its RMS detector
reports active audio. It begins after two seconds of signal and stops after 30
seconds of inactivity; incomplete windows are discarded at that intentional
boundary. Completed chunks are atomically added to `~/aswitch/groovenet-spool`;
a background worker posts them in order and retries network and 5xx failures
with exponential backoff. The filename carries the window start, session UUID,
and sequence number, so a restart or outage can resume safely using Groovenet's
idempotency key. Accepted and permanently rejected (4xx) chunks are deleted.

The settings live in `ansible/group_vars/aswitch.yml`. The capture device is
the existing `AUDIO_DEVICE` (currently the USB Audio CODEC) and capture is
forced to 44.1 kHz while Groovenet ingest is enabled. It requires Groovenet
v0.2.2 or later, which provides `/api/audio/ingest`.

CamillaDSP is deliberately disabled only on `aswitch.local` via
`camilladsp_enabled: false`. Its role, binaries, presets, and configuration are
preserved. Shairport's dependency is also removed while it is disabled. Set
that value to `true` and deploy the aswitch playbook to restore and start it.
While disabled, Shairport bypasses the loopback and sends AirPlay through the
UCA202's TOSLINK output to the externally powered SMSL SU-1. The SMSL has fixed
output, so Shairport uses a -12 dB maximum software level on aswitch to match
the mixer; adjust `shairport_volume_max_db` in small increments if the source
levels still differ.

## Repository layout

```
.
├── ansible/
│   ├── ansible.cfg
│   ├── inventory.ini           # gitignored — copy from inventory.ini.example
│   ├── requirements.yml        # ansible-galaxy collections
│   ├── deploy.yml              # beelink Docker stack playbook
│   ├── deploy-groovenet.yml    # GrooveNET stack playbook (tag-pinned GHCR images)
│   ├── group_vars/
│   │   ├── all/main.yml        # shared vars (versions, ports, MQTT)
│   │   ├── aswitch.yml         # aswitch-specific vars
│   │   ├── pi_cam.yml          # pi-cam-specific vars
│   │   └── townhaus_caddy/     # beelink vars (gitignored, copy from *.example)
│   ├── playbooks/
│   │   ├── beelink.yml         # AdGuard DNS + Beszel agent + Frigate config
│   │   ├── aswitch.yml         # all aswitch services
│   │   ├── aswitch_airplay.yml # focused aswitch Shairport deployment
│   │   ├── aswitch_ingest.yml  # focused aswitch ingest deployment
│   │   └── pi_cam.yml          # all pi-cam services
│   └── roles/
│       ├── aswitch_services/   # Python services + venv + env from 1Password
│       ├── camilladsp/         # CamillaDSP + CamillaGUI install and config
│       ├── shairport/          # shairport-sync.conf template
│       ├── frigate/            # Frigate config template + 1Password secrets
│       ├── beszel_hub/         # smartmontools for SMART monitoring
│       ├── beszel_agent/       # Beszel agent binary + service + Hub SSH key
│       └── adguard_dns/        # declarative DNS rewrites via AdGuard API
├── camilladsp/
│   └── configs/                # reference EQ presets (scp'd from aswitch)
├── services/
│   ├── aswitch/                # Python source (git subtree from aswitch repo)
│   │   ├── aswitch.py
│   │   ├── audio_activity.py
│   │   ├── dac_status.py
│   │   ├── home_assistant/     # example HA YAML
│   │   └── esphome/            # ESPHome IR blaster config
│   └── frigate/                # Frigate infra (git subtree from frigate-beelink)
│       ├── docker-compose.yml  # standalone compose (superseded by root compose)
│       └── config/             # config template reference
├── Caddyfile
├── docker-compose.yml
└── docs/                       # operator runbooks
```

## Prerequisites

```bash
brew install ansible 1password-cli just
ansible-galaxy collection install -r ansible/requirements.yml
op signin
```

## First-time setup

```bash
# Inventory
cp ansible/inventory.ini.example ansible/inventory.ini
# Edit ansible/inventory.ini — add ansible_host=<IP> for any host where mDNS is unreliable

# beelink vars
cp ansible/group_vars/townhaus_caddy/main.yml.example ansible/group_vars/townhaus_caddy/main.yml
cp ansible/group_vars/townhaus_caddy/beszel.yml.example ansible/group_vars/townhaus_caddy/beszel.yml
# Edit both files — set 1Password refs, beelink LAN IP, AdGuard credentials
```

**1Password items required** (all in the `Homelab` vault):

| Item | Fields |
|---|---|
| `Immich Database` | `password` |
| `Backblaze Immich` | `key_id`, `application_key`, `restic_password` |
| `MQTT` | `username`, `password` |
| `AdGuard` | `username`, `password` |
| `Frigate` | `mqtt_password`, `doorbell_rtsp_url`, `doorbell_talk_rtsp_url`, `backyard_rtsp_url`, `backyard_rtsp_sub_url`, `oficina_rtsp_url`, `oficina_rtsp_sub_url`, `homekit_pin` |

## Deploying

```bash
# Show every available command
just

# Deploy the Docker stack and host roles on beelink
just deploy-beelink

# Deploy either Pi
just deploy-aswitch
just deploy-pi-cam

# Deploy all hosts
just deploy

# Deploy the GrooveNET stack at the pinned tag (see "GrooveNET stack" below)
just deploy-groovenet
just deploy-groovenet vX.Y.Z   # one-off tag override

# Validate playbook syntax and the working diff
just check
```

## Pre-commit linting

Install the repository hooks once to run YAML checks and `ansible-lint --fix`
before each commit:

```bash
pipx install --python "$(mise which python3)" pre-commit  # if not already installed
pre-commit install
```

Install the project's Ansible collections first with `just dependencies`.

Run the Ansible lint check manually with `just lint`, or apply its available
automatic fixes with `just lint-fix`.

The lower-level `just deploy-stack` and `just configure-beelink` recipes are
available when only the Compose stack or host roles need to change.

## Operator runbooks

- [aswitch audio and power](docs/aswitch.md) — signal path, focused deploys,
  logs, and power diagnostics.
- [GrooveNET](docs/groovenet.md) — pinned image deployments and vinyl ingest
  verification.
- [Operations](docs/operations.md) — Uptime Kuma, backups, and Dependabot.

For Beelink, deploy the Caddy stack through Ansible. The playbook renders the
remote `.env` from `ansible/group_vars/townhaus_caddy/main.yml` and should
carry `GROOVENET_DOCKER_NETWORK=dj-playlist_default` and
`GROOVENET_UPSTREAM_HOST=webapp` there. The playbook also creates the shared
Docker network if it does not already exist.

If you intentionally start only Caddy locally instead of using Ansible, pass
those values in the shell environment, for example:

```bash
GROOVENET_DOCKER_NETWORK=dj-playlist_default \
GROOVENET_UPSTREAM_HOST=webapp \
docker compose up -d caddy
```

## GrooveNET stack

GrooveNET runs as its own Compose stack in `/srv/docker/groovenet`, separate
from the Caddy stack above. The Caddy stack only *attaches* to GrooveNET's
network (`dj-playlist_default`) to reverse-proxy `https://groovenet` to the
`webapp` container — it does not manage GrooveNET's containers.

`ansible/deploy-groovenet.yml` deploys pre-built GHCR images pinned to an image
tag, so the box never needs a clone of the GrooveNET repo. On the control
machine it shallow-clones the configured source ref (by default `main`) of
[`Public-Vinyl-Radio/groovenet`](https://github.com/Public-Vinyl-Radio/groovenet),
renders `.env` from 1Password with the repo's own
`scripts/render-env.sh`, copies the compose files + `.env` to the box, then runs
the same ordered sequence as the repo's `deploy-prod.sh`: pull → start
`db`/`redis` → wait for Postgres → run migrations → `up -d`.

Pin the version in `ansible/group_vars/townhaus_caddy/groovenet.yml`:

```yaml
groovenet_image_tag: vX.Y.Z   # a published GHCR image tag
# Optional: use a specific Git ref for the Compose files and .env.tpl.
groovenet_source_ref: main
```

Then deploy:

```bash
just deploy-groovenet          # uses the pinned groovenet_image_tag
just deploy-groovenet vX.Y.Z   # one-off override without editing group_vars
```

**Requirements:** on the control machine, `op` must be signed in (`op whoami`)
and you need SSH access to the GrooveNET repo. All GrooveNET secrets live in the
`Homelab` vault's `groovenet` item, referenced by the repo's `.env.tpl`.

This is a drop-in replacement for running `just deploy vX.Y.Z` from the
GrooveNET repo. It reuses the existing data volumes in place, so it's a
mechanism swap, not a fresh install.

## Dotfiles

Linux hosts can optionally install your dotfiles repo into `~/.dotfiles` and
apply the Stow-managed files into the target user's home directory.

The Ansible role is opt-in and assumes Debian-family hosts. It installs the
required packages, clones the repo, runs `stow --restow` for the configured
packages, and can set the user's default shell to `zsh`.

To enable it for a host group, add variables to the relevant group vars file:

```yaml
dotfiles_enabled: true
dotfiles_repo: https://github.com/saegey/dotfiles.git
```

For example:

- `ansible/group_vars/aswitch.yml`
- `ansible/group_vars/pi_cam.yml`
- `ansible/group_vars/townhaus_caddy/main.yml`

Because the repo is public, the default uses HTTPS and does not require SSH
keys on the target hosts. The clone is also shallow by default (`depth: 1`,
single branch) because the hosts only need the current dotfiles tree, not full
history.

The role intentionally does not run the dotfiles repo's interactive bootstrap
scripts. Those scripts call `sudo` internally, which does not compose cleanly
with Ansible's privilege escalation. The role applies the Stow-managed files
directly and installs the Debian-side prerequisites itself.

## CamillaDSP presets

Seven EQ presets are managed in `ansible/roles/camilladsp/templates/configs/`.
They are active on pi-cam; the aswitch configuration is preserved but CamillaDSP
is currently disabled there. The ALSA capture/playback devices are set per-host
in `group_vars`:

| Preset | Description |
|---|---|
| `camilla.yml` | Default — light bass boost |
| `flat.yml` | No processing |
| `reference.yml` | Flat bass/treble at 0 dB |
| `tonecontrol.yml` | Adjustable bass/treble |
| `late-night.yml` | Bass and treble lift for low volumes |
| `relaxed.yml` | Slight presence cut |
| `warm.yml` | Bass lift, treble cut |

Switch pi-cam presets via the CamillaGUI web UI (`https://pi-cam`). To restore
CamillaDSP on aswitch, set `camilladsp_enabled: true` and deploy aswitch.

## Pi-cam audio streamer

`pi-cam.local` is managed through the existing `shairport`, `camilladsp`, and
`aswitch_services` roles in `ansible/playbooks/pi_cam.yml`. The intended signal
path is:

```text
AirPlay -> Shairport Sync -> ALSA Loopback -> CamillaDSP -> Fosi ZD3 -> amp
```

The `pi_wifi` role disables Wi-Fi power saving on pi-cam's existing
NetworkManager connection (`preconfigured`). It intentionally leaves the SSID
and password unmanaged, so UniFi remains the source of truth for network
policy. If the connection name changes, override `pi_wifi_connection_name` in
`ansible/group_vars/pi_cam.yml`. NetworkManager applies the power-save setting
when the connection next activates, so the role does not interrupt an active
Wi-Fi session to force it.

## Centralized logs

Grafana and Loki run on the Beelink. Grafana Alloy runs as a systemd agent on
the Beelink and both Pis. Alloy forwards each host's systemd journal, including
kernel, NetworkManager, and application-service events. On the Beelink, Alloy
also discovers and forwards every Docker container's logs; individual Compose
services need no additional logging configuration.

Loki retains logs for 14 days. Grafana is available at `https://grafana` and
`https://grafana.home.arpa`. Loki's unauthenticated ingest API is intentionally
LAN-only at `http://loki.home.arpa:3100`; do not expose port 3100 to the
internet.

### Setup and deployment

Before deploying the stack, create a `Grafana` item in the `Homelab` 1Password
vault with a `password` field and set this reference in the untracked
`ansible/group_vars/townhaus_caddy/main.yml`:

```yaml
townhaus_caddy_grafana_admin_password_ref: op://Homelab/Grafana/password
```

Deploy the stack and agents:

```bash
just deploy-beelink
just deploy-aswitch
just deploy-pi-cam
```

After a configuration update, verify the collector path from a Pi:

```bash
getent hosts loki.home.arpa
curl -fsS http://loki.home.arpa:3100/ready
sudo systemctl status alloy --no-pager
sudo journalctl -u alloy -n 100 --no-pager
```

The first Alloy start forwards up to 24 hours of available journal entries;
new entries normally appear in Grafana within 10–30 seconds.

### Exploring logs

Sign in at `https://grafana` as `admin` using that 1Password password. Open
**Explore**, select the automatically provisioned **Loki** data source, and use
LogQL queries such as:

```logql
{host="pi-cam.local"}
{host="pi-cam.local", unit="NetworkManager.service"}
{host="pi-cam.local"} |= "wlan0"
{host="beelink.local", job="docker"}
{host="beelink.local", job="docker", container="caddy"}
```

If Grafana reports it cannot resolve `loki`, check that the Loki container is
healthy on the Beelink:

```bash
cd /srv/docker/townhaus-caddy
docker compose ps loki grafana
docker compose logs --tail=100 loki
```

For `pi-cam`, Shairport Sync is configured to publish MQTT metadata while
leaving the PCM stream at full scale. Home Assistant should consume the raw
AirPlay volume metadata from MQTT and translate that into ZD3 IR volume
commands; Shairport itself should not attenuate the audio signal digitally.

Deploy and validate:

```bash
just deploy-pi-cam
just status-pi-cam
just syntax-check
```

Useful checks on the Pi:

```bash
systemctl status shairport-sync camilladsp --no-pager
journalctl -u shairport-sync -f
journalctl -u camilladsp -f
shairport-sync -V
aplay -l
aplay -L
arecord -l
cat /proc/asound/cards
```

MQTT inspection:

```bash
mosquitto_sub -h MQTT_HOST -u MQTT_USERNAME -P MQTT_PASSWORD \
  -t 'pi-cam/shairport/#' -v
```

Expected `pi-cam` behavior:

- The base Shairport topic is `pi-cam/shairport`.
- AirPlay-requested volume should be published to MQTT in the format emitted by
  the installed `shairport-sync` build, including mute sentinels if present.
- `shairport-sync.service` is ordered after `camilladsp.service` so playback
  does not start before the DSP path is available.
- The rendered Shairport config is restricted to root-readable permissions
  because it contains MQTT credentials.

Physical Pi facts still to verify after deployment:

- The exact `shairport-sync -V` output on `pi-cam`, including reported MQTT
  support and any version-specific config syntax differences.
- The exact MQTT subtopics and payloads emitted by the installed build for
  volume, metadata, availability, and session state.
- Whether `plughw:CARD=ZD3,DEV=0` and `hw:Loopback,1` remain stable across
  reboot and USB reconnect events on this Pi.
- Whether the stream-start pop is improved by holding CamillaDSP open and
  fixing the Shairport output sample rate/format to `44100` and `S32_LE`.
- Whether additional mitigation is still needed at the DAC or DSP layer.

## Frigate NVR

Frigate runs as a Docker service on beelink with `network_mode: host` (required for go2rtc/HomeKit local discovery). The config is deployed to `/srv/storage/frigate-config/config.yml` by the Ansible `frigate` role.

All credentials (MQTT password, camera RTSP URLs, HomeKit PIN) are stored in 1Password and rendered into the config at deploy time — no plaintext secrets in the repo.

### Upgrade to Frigate 0.18.0

`just deploy-beelink` performs the 0.18 upgrade. Before it pulls and starts the
new pinned image, it detects a `0.17-0` runtime configuration, stops Frigate,
and creates a consistent one-time backup at
`/srv/storage/frigate-backups/v0.17-pre-v0.18`. This includes both `config.yml`
and `frigate.db`.

After the deploy, check the Frigate UI and logs with `just status` and
`just logs frigate`. The config is updated for Frigate 0.18's FFmpeg 8/go2rtc
VAAPI behavior. 0.18 also changes on-disk snapshots to clean WebP files; use
the event snapshot API if another service requires annotated JPEG snapshots.

```bash
# First-time setup
cp ansible/group_vars/townhaus_caddy/frigate.yml.example ansible/group_vars/townhaus_caddy/frigate.yml
# Edit frigate.yml — set frigate_mqtt_host, email, and 1Password refs

# Deploy config + start container
just deploy-beelink
```

## Package update monitoring

Each managed Linux host runs a weekly APT audit on Monday morning. It refreshes
package metadata but never installs packages or restarts a host.
It publishes `sensor.<host>_package_updates` and
`binary_sensor.<host>_reboot_required` to Home Assistant, including the pending
package names, running kernel, kernel metapackage version, and last check time.
When updates, a reboot requirement, or an APT refresh failure are found, it
updates a single Home Assistant persistent notification for that host.

The reporting role uses the existing `HomeAssistantNotifier` long-lived token
in 1Password. Trigger a check at any time with:

```bash
just check-updates
```

## Immich backups

Immich is backed up to a private Backblaze B2 bucket with Restic. The backup
role creates a fresh compressed PostgreSQL dump first, then takes an encrypted,
deduplicated snapshot of both the dump and the complete
`IMMICH_UPLOAD_LOCATION`. It retains 7 daily, 5 weekly, and 12 monthly
snapshots. A weekly repository check reads and verifies 5% of the stored data.

The role is opt-in. To configure it:

1. Create a private B2 bucket and note its S3 endpoint. Set its lifecycle rule
   to **Keep only the last version of the file**; Restic manages backup history.
   Do not enable Object Lock because it prevents Restic from pruning snapshots.
2. Create an application key restricted to that bucket with read/write access.
3. Create a `Backblaze Immich` item in the `Homelab` 1Password vault with
   `key_id`, `application_key`, and a separately generated `restic_password`.
   Keep that Restic password permanently; the repository cannot be recovered
   without it. From zsh, create the item without placing secrets in shell
   history or command arguments:

   ```zsh
   read "B2_KEY_ID?Backblaze application key ID: "
   read -s "B2_APPLICATION_KEY?Backblaze application key: " && printf '\n'
   RESTIC_PASSWORD="$(openssl rand -base64 48)"

   op item create \
     --category "Secure Note" \
     --title "Backblaze Immich" \
     --vault Homelab \
     "key_id[concealed]=$B2_KEY_ID" \
     "application_key[concealed]=$B2_APPLICATION_KEY" \
     "restic_password[concealed]=$RESTIC_PASSWORD"

   unset B2_KEY_ID B2_APPLICATION_KEY RESTIC_PASSWORD
   ```

   Verify that all three references resolve without revealing their values:

   ```bash
   for field in key_id application_key restic_password; do
     op read "op://Homelab/Backblaze Immich/$field" >/dev/null || exit 1
   done
   echo "Backblaze Immich credentials are available"
   ```

4. Copy and edit the variables file:

   ```bash
   cp ansible/group_vars/townhaus_caddy/immich_backup.yml.example \
     ansible/group_vars/townhaus_caddy/immich_backup.yml
   ```

5. Deploy the host roles and run the first backup manually:

   ```bash
   just deploy-beelink
   just immich-backup
   just immich-backup-logs
   ```

The daily backup runs around 03:30 local time and the integrity check runs
weekly. Inspect the schedules and most recent result with:

```bash
just immich-backup-status
```

For a restore, recover both `/srv/storage/immich/library` and
`/var/lib/immich-backup/immich-database.sql.gz` from the same Restic snapshot,
then follow Immich's database restore procedure. Test this process before
treating the backup as production-ready.

## AdGuard DNS

DNS rewrites are managed declaratively in `ansible/group_vars/townhaus_caddy/beszel.yml` under `adguard_dns_rewrites`. All short hostnames resolve to beelink's LAN IP; Caddy handles the reverse proxy. Run the beelink playbook to apply changes.

## Uptime Kuma upgrades

Uptime Kuma tracks the supported v2 image tag. On the first deployment from v1,
the stack temporarily stops Uptime Kuma and creates a consistent archive of its
Docker data volume at `/srv/storage/uptime-kuma-backups/uptime-kuma-v1-pre-upgrade.tar.gz`
before pulling and starting v2. Copy this archive off the Beelink before the
upgrade if it is needed for disaster recovery; it is a local rollback backup,
not an off-site backup.

## Container image updates

Dependabot checks the root Compose file and the standalone Frigate Compose
file weekly. Every production image is pinned as `release@sha256:digest`: the
release keeps Dependabot updates readable, while the digest ensures a deploy
always pulls the reviewed artifact. Do not use `latest`, a major-only tag, or a
tag without a digest in a production Compose file.

Review image release notes and backup or migration requirements before merging
an update, especially for Immich and Frigate. Deploy an approved update with:

```bash
just deploy-beelink
```

If an image update must be rolled back, revert the commit that changed its
image reference (or restore its exact prior `release@sha256:digest` value),
then redeploy. The prior digest is immutable, so this restores the exact
previous artifact rather than whatever its old tag now points to. Consult the
service's migration and backup notes before rolling back any application that
has changed on-disk data.

## Beszel monitoring

Beszel Hub runs as a Docker container on beelink (`https://beszel`). Agents run on all three hosts. The Hub's SSH public key is automatically distributed to each agent via the Ansible playbooks — no manual key copying needed.

After running the playbooks, add each host in the Beszel UI (**Add System**) with port `45876`.

beelink runs the agent as root for SMART disk monitoring (`smartmontools` installed automatically).

## Caddy internal TLS

Caddy issues certs from its own local CA. Trust the root cert on your devices:

```bash
mkdir -p ./certs
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./certs/caddy-root.crt

# macOS
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ./certs/caddy-root.crt

# Debian/Ubuntu
sudo cp ./certs/caddy-root.crt /usr/local/share/ca-certificates/caddy-root.crt
sudo update-ca-certificates
```

## Useful commands

```bash
# Reload Caddy config without downtime
just caddy-reload

# Follow logs
just logs caddy
just logs adguardhome
just logs immich-server
just logs beszel-hub

# Check Pi service status
just status-aswitch
just status-pi-cam
```

## Home Assistant reverse proxy

Add to `configuration.yaml`:

```yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 172.16.0.0/12
    - 192.168.0.0/16
    - 10.0.0.0/8
```
