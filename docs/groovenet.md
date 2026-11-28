# GrooveNET operations

GrooveNET runs as its own Compose stack on beelink at
`/srv/docker/groovenet`. Caddy attaches to its shared Docker network and
reverse-proxies `https://groovenet`; the root Compose stack does not manage its
containers.

## Deploy a release

Deploy from the **Groovenet repository**, using its `just deploy` or
`just deploy-rc` workflow. That deploy owns the current Compose model and reads
host-local `.env.host` settings, adding the local backup overlay when
`LOCAL_BACKUP_PATH` is set. It preserves `.env.host` across releases.

**Superseded:** this repository's `ansible/deploy-groovenet.yml` and
`just deploy-groovenet` recipe are legacy deployment paths. They pin an older
image and copy only the base and production Compose files, ignoring `.env.host`
and `docker-compose.local-backup.yml`. Do not use them for this host: they can
remove the local backup configuration from the running app. Their tracked image
pin is retained for historical context, not as the current production version.
The storage provisioning recipe `just deploy-groovenet-backup-disk` is separate
and remains supported; it never deploys or restarts the application.

## Legacy Ansible server-player preservation

The superseded Ansible playbook detects the existing `.env.players` and
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
present, then use Groovenet's supported deployment workflow with the intended
image tag. If player containers were removed, the Compose model recreates
them using their existing persistent volumes. This can restart audio services;
choose an appropriate maintenance time.

Run `just test-groovenet-deploy` for isolated deployment-policy regressions. The
tests use temporary files and a fake Docker executable, without contacting a host.

## Local lossless backup on the shared storage disk

The 36 GB lossless library can use the existing 4 TB Seagate
ST4000DM004-2U9104 (serial `ZW641752`) that also holds Immich photos and Frigate
recordings. Inspection found 2.8 TB free. Its ext4 filesystem UUID is
`5b4a169a-a2b9-4f21-81a9-1e2e815d4c39`, mounted at `/srv/storage`.
The primary library is on the separate NVMe/root disk; lossless files are
excluded from the remote B2 backup by default. Losing both disks loses these
local lossless copies.

The dedicated `groovenet_backup_disk` role reuses this filesystem only: it has
no partitioning or formatting tasks. It requires the expected UUID to already
be mounted on a different device from `/srv`, creates
`/srv/storage/groovenet-backup` owned by `1001:1001` with mode `0700`, and
bind-mounts that directory at `/srv/groovenet-backup`. Only that directory is
exposed to Groovenet. The shared disk root and existing service directories
retain their ownership and permissions.

Both mounts are persisted with `nofail`; the UUID entry also has
`x-systemd.device-timeout=10s`. The bind mount requires `/srv/storage` so systemd
orders it after the storage mount. The role updates the shared disk's fstab
entry without remounting it, then verifies the bind mount's UUID, filesystem
root and separate device before creating `.groovenet-backup-disk` on it.
The first app backup creates `groovenet-restic/` beneath it. If the disk is
absent, the role fails before writing and the app's missing marker prevents
backups falling back to the root disk.

Setup:

1. Copy `ansible/group_vars/townhaus_caddy/groovenet_backup_disk.yml.example`
   to `groovenet_backup_disk.yml` in the same directory (gitignored).
2. Verify the UUID with `lsblk` and `findmnt`. Store a new, independent local
   Restic password in 1Password and set `groovenet_backup_disk_restic_password_ref`
   to its exact `op://` reference. The configured field is
   `op://Homelab/groovenet/RESTIC_LOCAL_PASSWORD`.
   Keep it outside the host: changing this password does not rekey an existing
   repository and would prevent the app opening its backups.
3. For read-only storage checks without sudo or secret access, run
   `just deploy-groovenet-backup-disk --check --diff --tags preflight -e ansible_become=false -e ansible_become_password=`.
   Then run `just deploy-groovenet-backup-disk --check --diff --ask-become-pass` and
   review the result before applying. Check mode proposes the directory/mount
   changes; mounted-target verification and marker creation run only on apply.
4. With owner approval, run
   `just deploy-groovenet-backup-disk --ask-become-pass`.
   This standalone playbook touches no running services, does not deploy the
   app, and never edits `.env`. It preserves other `.env.host` settings,
   managing only `LOCAL_BACKUP_PATH` and `LOCAL_BACKUP_RESTIC_PASSWORD`, with
   `.env.host` mode `0600` and ownership taken from `.env`.
5. Verify on Beelink:

   ```bash
   findmnt --mountpoint /srv/storage
   findmnt --mountpoint /srv/groovenet-backup
   sudo ls -la /srv/groovenet-backup/.groovenet-backup-disk
   sudo stat -c '%u:%g' /srv/groovenet-backup
   sudo sed 's/^LOCAL_BACKUP_RESTIC_PASSWORD=.*/LOCAL_BACKUP_RESTIC_PASSWORD=<redacted>/' /srv/docker/groovenet/.env.host
   grep -E '/srv/(storage|groovenet-backup) ' /etc/fstab
   ```

   Expect `1001:1001` and `nofail` on both fstab entries. Rerun the playbook
   and expect zero changes. The mount settings allow booting without the disk;
   the owner can verify a reboot at a suitable time.
6. Deploy from the Groovenet repository with `just deploy` / `just deploy-rc`
   so its deploy script reads `.env.host` and adds
   `docker-compose.local-backup.yml`. The superseded Ansible deploy ignores
   these settings and cannot enable local backups. Then run
   **System → Backups → Backup** and confirm the **Local disk** row is healthy.

The existing Kuma **Groovenet backup** monitor checks
`GET /api/health/backup`; no additional monitor is needed. No Terraform changes
are needed: this is host storage configuration.

To replace the disk, arrange migration of all shared storage data with the
owner first, retain the Restic password, mount the replacement at `/srv/storage`,
update the UUID in the private vars file, and review another check run. Any
formatting requires separate approval naming the exact device and serial;
this role cannot do it. Preserve/copy `groovenet-backup/groovenet-restic` if
keeping snapshots, and rerun the role to verify the mount and marker. Never
create a marker on an unmounted fallback directory. See Groovenet's
[local disk and FLAC restore instructions](https://github.com/Public-Vinyl-Radio/groovenet/blob/main/docs/configuration/integrations.md#restoring-flacs-from-the-local-repository)
for restoring into a scratch directory before copying audio back.

## Vinyl ingest

`audio_activity.service` on aswitch uploads 15-second mono WAV windows to
`/api/audio/ingest` only while audio activity is present. It starts after two
seconds of activity and keeps a 30-second inactive hold. Windows wait in a local
spool and upload in order, with backoff (2 s doubling to 5 min) on failure.

What happens to a window depends on GrooveNET's answer (its ingest contract,
groovenet#1227):

| Response | Window |
|---|---|
| 2xx | Accepted; removed from the spool |
| 400 `missing_audio_file`, `missing_source_id`, `unsupported_audio_format`, `invalid_audio_stream`, `audio_too_short`, `audio_too_long`; 413 `audio_too_large` | Bad window; deleted |
| 408 `upload_incomplete`, 429, 5xx, a network error or timeout | Kept and retried |
| Any other 4xx (an unknown code, or a body that isn't GrooveNET's JSON) | Retried; after `GROOVENET_MAX_UNRECOGNIZED_ATTEMPTS` (default 10) it moves to `groovenet-spool/rejected/` so later windows aren't blocked |

```bash
ssh aswitch.local 'journalctl -u audio_activity -n 100 --no-pager'
ssh aswitch.local 'find ~/aswitch/groovenet-spool -maxdepth 1 -type f | wc -l'
ssh aswitch.local 'ls ~/aswitch/groovenet-spool/rejected 2>/dev/null'
```

Persistent spool growth means the endpoint, TLS trust, or server response needs
attention. Windows in `rejected/` are kept, not deleted. Clear them by hand once
inspected, or move them back into `groovenet-spool/` to replay them.
