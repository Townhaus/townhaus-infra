# GrooveNET operations

GrooveNET runs as its own Compose stack on beelink at
`/srv/docker/groovenet`. Caddy attaches to its shared Docker network and
reverse-proxies `https://groovenet`; the root Compose stack does not manage its
containers.

## Deploy a release

The tracked image pin is `groovenet_image_tag` in
`ansible/group_vars/townhaus_caddy/groovenet.yml`. Update that value in a
reviewed commit, then deploy it:

```bash
just deploy-groovenet
```

For a one-off test without changing the tracked pin:

```bash
just deploy-groovenet vX.Y.Z
```

The override updates the running stack only. It does not edit the tracked pin,
so update and merge the configuration before relying on that release for future
deployments.

The playbook uses `groovenet_source_ref` to fetch Compose files and `.env.tpl`.
That ref is intentionally separate from the image tag.

## Preserve server players during deployments

The playbook detects the existing `.env.players` and
`docker-compose.players.yml` in `/srv/docker/groovenet` before changing the
stack. When present, both are included in every Compose operation, so the app
receives `PLAYER_API_KEY` / `PLAYER_WORKER_URL` and player services remain in
the deployment model during `--remove-orphans`. The authenticated, read-only
`GET /api/players` check must pass after startup; it sends no playback commands.

These files, engine sources and locally built player images remain host-managed.
The collection `.env` still refreshes from 1Password, but does not replace player
settings. A normal deployment uses `--no-build`; provision missing player images
through GrooveNET's server-player setup workflow before deploying.

A host with neither file and no player containers remains opted out. If only one
file exists, or player containers exist without their settings, deployment fails
before rendering credentials or restarting anything. Restore the missing files;
do not create an empty `.env.players` to bypass this check. Browser credentials
in `.env.player-web` also remain host-managed and are read by the installed overlay.

If the CLI reports `PLAYERS_DISABLED` after an older deployment, the app was
started without a valid worker key or URL; this error does not imply deleted
player database rows. Confirm both player files and existing engine images are
present, then rerun `just deploy-groovenet` with the same running image tag using
the fixed playbook. If player containers were removed, the Compose model recreates
them using their existing persistent volumes. This can restart audio services;
choose an appropriate maintenance time.

Run `just test-groovenet-deploy` for isolated deployment-policy regressions. The
tests use temporary files and a fake Docker executable, without contacting a host.

## Vinyl ingest

`audio_activity.service` on aswitch uploads 15-second mono WAV windows to
`/api/audio/ingest` only while audio activity is present. It starts after two
seconds of activity, keeps a 30-second inactive hold, and retries network or
5xx failures from its local spool.

```bash
ssh aswitch.local 'journalctl -u audio_activity -n 100 --no-pager'
ssh aswitch.local 'find ~/aswitch/groovenet-spool -maxdepth 1 -type f | wc -l'
```

Successful requests are accepted by GrooveNET and removed from the spool.
Persistent spool growth means the endpoint, TLS trust, or server response needs
attention.
