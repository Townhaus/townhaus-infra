# Routine operations

## Uptime Kuma

Uptime Kuma is configured from
`ansible/group_vars/townhaus_caddy/uptime_kuma.yml` (a gitignored local file).
Apply only monitor and status-page changes with:

```bash
just configure-uptime-kuma
```

The Vinyl Live Compose stack has a systemd boot unit so its containers are
reconciled after Docker starts, including when a container was removed. Apply
only that boot-unit change with:

```bash
ansible-playbook ansible/deploy-vinyl-live.yml --limit townhaus_caddy \
  --tags vinyl_live_boot --ask-become-pass
```

The aswitch monitor is an ICMP Ping check for `192.168.2.170`. It should not
monitor the retired CamillaGUI port.

## Immich backups

```bash
just immich-backup
just immich-backup-status
just immich-backup-logs
```

The detailed setup and restore notes remain in the repository README. Treat a
backup as production-ready only after testing a restore.

## Dependency updates

Dependabot checks the root Compose file and the standalone Frigate Compose
file weekly. Keep container image tags literal in tracked Compose files so it
can open reviewable update PRs.

To request a run after a change reaches `main`, open the repository on GitHub:
**Insights → Dependency graph → Dependabot → Recent update jobs → Check for
updates**.

Review image release notes and backup/migration requirements before merging an
application update, especially for Immich and Frigate.
