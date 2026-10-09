# PVR Analog deployment

PVR Analog is the Public Vinyl Radio CRT now playing display. Its application
source lives in [Public-Vinyl-Radio/now-playing](https://github.com/Public-Vinyl-Radio/now-playing).
This infrastructure repo deploys it to `/srv/docker/now-playing` on the Beelink.
It displays HA metadata; it does not control playback.

## Deploy from the Mac

With the existing Caddy stack running and your local Ansible inventory and
Beelink variables configured:

```sh
cd ~/Projects/townhaus-caddy
just deploy-now-playing
```

The Beelink needs Docker Compose v2 with `up --wait` support and access to
GitHub, Docker Hub, and the npm registry for the source build. Git is installed
by the playbook if needed. No application image publishing step is required.

The command asks for the Beelink sudo password to create the application
directory and install the managed Caddyfile. 1Password may also request
approval on the Mac. You need access to the HA token and the existing AdGuard
credential references. No 1Password installation is required on the Beelink.

The playbook:

1. Validates the existing Caddy container, network, and proposed configuration.
2. Fetches and checks out the latest public app `main` (or the supplied commit/tag)
   into `/srv/docker/now-playing/source` and prints the resolved commit SHA.
3. Resolves `op://Homelab/Home Assistant Codex Token/token` on the Mac and writes
   the server `.env` on the Beelink with mode `0600`, suppressing secret output
   and diffs.
4. Copies only Caddy's public root certificate into the app's `certs` directory.
5. Builds with the app's Dockerfile and waits for the container's HTTP health check.
6. Installs the managed Caddyfile while preserving its bind mount and reloads
   Caddy. The complete tracked Caddyfile is applied, so review any other pending
   Caddyfile edits before deploying.
7. Applies only the `now-playing.home.arpa` AdGuard rewrite using the existing
   Beelink DNS address.

Other Compose services are not pulled or recreated by this playbook.

## Configuration

Defaults live in [now_playing.yml](../ansible/group_vars/townhaus_caddy/now_playing.yml).
Each deployment fetches the latest `main` from GitHub. Push or merge application
changes to that branch before running `just deploy-now-playing`; local unpushed
changes in the separate application repo are not deployed. To deploy a specific
commit or a published `vX.Y.Z` Git release tag:

```sh
just deploy-now-playing <full-commit-sha-or-release-tag>
```

Set `now_playing_source_ref` in the variables file to a full commit SHA or release
tag if deployments should stay pinned. The command prints the exact commit being
built and deployed. It builds from source and does not require a published
application image in GHCR.

The HA defaults are:

- URL: `https://ha.home.arpa`
- Player: `media_player.living_room_audio`
- Vinyl indicator: `binary_sensor.vinyl_listening_active` (`on` means vinyl,
  `off` means streaming, and unknown or unavailable remains unknown).

`now_playing_data_mode: mock` selects bundled preview metadata instead of HA.
`now_playing_ha_artwork_origins` optionally allows comma-separated HTTPS origins
for external cover art; HA itself is already allowed. Use the app's hidden
picture controls to select mock input temporarily without redeployment.

Run the deployment again after rotating the token in 1Password. The token is
present only in the server runtime environment and the private Beelink `.env`;
it is excluded from the source checkout, build context, logs, and browser.
Docker administrators and the configured file owner can access the runtime secret.

## Networking and certificate trust

The app joins Caddy's existing `townhaus-caddy_default` network and exposes no
host port. This avoids the app's standalone default port `3010`, which Uptime
Kuma already uses. Caddy proxies to `now-playing:3000`; no new network attachment
or Caddy container recreation is needed.

`ha.home.arpa` is mapped to the Beelink's host gateway inside the app container,
so HA requests reach the existing Caddy route without relying on container DNS
for that hostname. HTTPS certificate verification remains enabled.
`NODE_EXTRA_CA_CERTS` points to the public Caddy root mounted read-only into the
app. Only the certificate is copied; Caddy's private keys are never mounted.
Deploy again after a CA change so the container restarts with the new trust data.

Open **https://now-playing.home.arpa** in landscape Safari, then optionally add
it to the Home Screen. If the iPad already trusts this Beelink Caddy CA for HA,
no new certificate installation is needed. Otherwise follow the repo's
[Caddy internal TLS instructions](../README.md#caddy-internal-tls), install the
public root on the iPad, and enable its trust in Certificate Trust Settings.
Remote tailnet access still needs your existing `home.arpa` DNS and routing.

There is no application login. Access relies on the existing LAN and tailnet
policy. The HA token stays server-side and the proxy offers no playback controls.

## Operations

```sh
just status-now-playing
just logs-now-playing
```

The HTTP health check confirms the app server is ready, not that HA credentials
are valid. In the iPad's picture controls, confirm **HOME ASSISTANT / CONNECTED**.
Idle playback shows station standby. Play music through the usual controls to
check real metadata, timing, and artwork. The signal trace remains procedural.

For a rollback, redeploy a previously working full commit SHA. No application
database or audio state needs migration.
