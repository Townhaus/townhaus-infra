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

The aswitch monitor is an ICMP Ping check for `aswitch.host.home.arpa`. It should not
monitor the retired CamillaGUI port.

## UPS monitoring

For the Beelink USB UPS and Home Assistant battery sensors, see [UPS setup](ups.md).

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

## Beszel system addresses

Beszel stores each agent's connection address in the hub, separately from DNS
and Uptime Kuma. The `beszel_hub` role reconciles the existing `Aswitch` and
`PiCam` records to their direct host DNS names. Configuration lives in
`ansible/group_vars/townhaus_caddy/beszel_systems.yml`.

The configured 1Password references select the existing `Homelab` Beszel login's
`email` and `passwordConfirm` fields by item ID. Update `beszel_hub_user_ref`
and `beszel_hub_password_ref` if that item changes. The role uses the
authenticated REST API to update the host and request a reconnect, retaining
system IDs, history, user access, agent keys, and ports.

```bash
just configure-beszel-systems
# Preview address changes without saving them:
ansible-playbook ansible/playbooks/beszel_systems.yml --check
```

The full Beelink configuration also reconciles these addresses after applying
DNS rewrites. No hub or agent restart is required for an address update.
Intentionally paused systems stay paused. To explicitly reconnect an existing
record without changing its address, pass
`-e '{"beszel_hub_reconnect_systems":["Aswitch"]}'` to the playbook.
