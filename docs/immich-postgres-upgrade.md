# Immich PostgreSQL 14 to 16

PostgreSQL 16 cannot read the existing PostgreSQL 14 data directory. Run this
migration before deploying the pending app updates. The normal stack deployment
now checks `PG_VERSION` and refuses incompatible images.

## Run

1. Check `just immich-backup-status` and trigger a fresh complete backup with
   `just immich-backup`. Wait for success. This migration creates another database
   dump, but does not copy the photo/video library.
2. Schedule downtime for the database dump, restore, index rebuild, row counts,
   and startup checks. Duration depends on database size.
3. From this checkout with your normal inventory and Beelink group variables:

   ```sh
   just upgrade-immich-postgres
   ```

   Equivalent command:

   ```sh
   ansible-playbook -i ansible/inventory.ini \
     ansible/playbooks/upgrade_immich_postgres.yml --ask-become-pass \
     -e immich_postgres_upgrade_confirm=true
   ```

4. Check existing photos in Immich before applying the app updates with
   `just deploy-stack`. Follow Immich's release notes for mobile client updates.

## Migration behavior

- Requires running `immich_postgres` and `immich_server` containers, a bind-mounted
  PostgreSQL 14 cluster, and a deployed Compose file still referencing PostgreSQL
  14. Reruns after successful migration fail without modifying the database.
- Uses the checkout's pinned PostgreSQL 16 image with VectorChord 0.4.3 and
  pgvecto.rs 0.2.0. Other variants are rejected. Requires Python 3, Bash, Docker
  Compose, gzip, flock, and standard GNU filesystem utilities on the host.
- Checks free space for the dump and staging directory. Pauses the backup timer,
  refuses to race an active backup service, and restores the previously active
  timer in Ansible's `always` block.
- Starts an isolated PostgreSQL 16 container without networking. Stops the current
  Immich server, checks for other database clients, and creates a compressed SQL
  dump. Restores into a new directory in one transaction, using Immich's documented
  extension search path adjustment.
- Compares row counts for every public base table before cutover and again after
  recreating the production database container. Runs `ANALYZE` on the new cluster.
- Renames the original cluster to `<original>.pg14-backup-<UTC timestamp>` and
  moves the restored cluster into the original path. Inventory and `.env` paths
  remain valid. Updates only the database image in the deployed Compose file.
  Restarts the existing app container and waits for it to become healthy.
- Keeps the current Immich app version during the database migration. The normal
  deployment can upgrade the app after this migration succeeds.

## Recovery

Private artifacts are stored in `/var/lib/immich-postgres-upgrade/<UTC timestamp>/`:
SQL dump, original Compose file, row counts, and `paths.txt`. They contain sensitive
content. Temporary database credentials are removed on exit. A `COMPLETE` file
marks a successful migration. Original clusters and dumps are never auto-deleted.

Before the application resumes, a failed command triggers an attempt to restore
the original directory and Compose file and restart PostgreSQL 14 and Immich.
Failed PostgreSQL 16 directories are preserved.

After the application resumes, new writes may exist in PostgreSQL 16. An unhealthy
application leaves that database in place and reports failure. It does not discard
new writes by automatically reverting to PostgreSQL 14. Investigate with Immich
offline before selecting a recovery point.

Power loss or forced termination can require manual recovery. Read `paths.txt`,
inspect both `PG_VERSION` files and the saved Compose file, and keep Immich stopped
until the intended database is selected. Retain the old cluster and dump until the
new cluster and its backups have been checked.

Offline regression tests cover dump/restore failures, row count mismatch, cutover
failure, and an unhealthy app. These simulate Docker; they do not establish
compatibility of the real extensions or production database. The staged restore
validates those during the maintenance window.

References: [PostgreSQL major upgrades](https://www.postgresql.org/docs/16/upgrading.html)
and [Immich backup and restore](https://docs.immich.app/administration/backup-and-restore/).
