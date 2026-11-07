#!/usr/bin/env bash
# Offline PG14 -> PG16 migration, invoked as root by Ansible.
set -Eeuo pipefail
umask 077
app_dir=$1
target_image=$2
state_root=${3:-/var/lib/immich-postgres-upgrade}
mkdir -p "$state_root"
exec 9>"$state_root/migration.lock"
flock -n 9 || { echo 'Another migration is running' >&2; exit 1; }
cd "$app_dir"
compose=(docker compose)
metadata=$(docker inspect immich_postgres)
project=$(printf '%s' "$metadata" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["Config"]["Labels"]["com.docker.compose.project"])')
[[ -n "$project" ]]
compose+=(--project-name "$project")
source_dir=$(printf '%s' "$metadata" | python3 -c 'import json,sys; print(next(m["Source"] for m in json.load(sys.stdin)[0]["Mounts"] if m["Destination"] == "/var/lib/postgresql/data" and m["Type"] == "bind"))')
[[ "$source_dir" = /* && "$(readlink -f "$source_dir")" = "$source_dir" ]]
configured_source=$("${compose[@]}" config --format=json | python3 -c 'import json,sys; print(next(v["source"] for v in json.load(sys.stdin)["services"]["immich-database"]["volumes"] if v["target"] == "/var/lib/postgresql/data" and v["type"] == "bind"))')
[[ "$(readlink -f "$configured_source")" = "$source_dir" ]] || { echo 'Compose and the running database have different data paths' >&2; exit 1; }
[[ "$(cat "$source_dir/PG_VERSION")" = 14 ]] || { echo 'Expected an existing PostgreSQL 14 directory' >&2; exit 1; }
[[ "$(docker inspect -f '{{.State.Running}}' immich_server)" = true ]]
[[ "$(docker inspect -f '{{.State.Running}}' immich_postgres)" = true ]]
[[ "$target_image" = ghcr.io/immich-app/postgres:16-vectorchord0.4.3-pgvectors0.2.0@sha256:* ]]
if systemctl is-active --quiet immich-backup.service; then
  echo 'An Immich backup is already running; wait for it to finish' >&2
  exit 1
fi
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$state_root/$stamp"
stage="${source_dir}.pg16-stage-$stamp"
old="${source_dir}.pg14-backup-$stamp"
temporary="immich-pg16-upgrade-$stamp"
source_bytes=$(du -sb "$source_dir" | cut -f1)
for location in "$(dirname "$source_dir")" /var/lib; do
  available=$(df -B1 --output=avail "$location" | tail -1)
  (( available > source_bytes * 2 + 1073741824 )) || { echo "Insufficient free space at $location" >&2; exit 1; }
done
mkdir -p "$backup" "$stage"
cp -p docker-compose.yml "$backup/docker-compose.yml"
# Change only the database image in the deployed Compose file.
python3 - "$target_image" "$backup/docker-compose.yml" "$backup/compose-pg16.yml" <<'PY'
import re,sys
image,source,target=sys.argv[1:]
text=open(source).read()
text,count=re.subn(r'(?m)^([ \t]*image:[ \t]*)ghcr\.io/immich-app/postgres:14[^\s]+[ \t]*$',lambda m:m[1]+image,text)
if count != 1:
    raise SystemExit('Expected exactly one PostgreSQL 14 image in deployed Compose file')
open(target,'w').write(text+'\n')
PY
trap 'rm -f "$backup/database.env"' EXIT
printf '%s' "$metadata" | python3 -c 'import json,sys; print("\n".join(e for e in json.load(sys.stdin)[0]["Config"]["Env"] if e.startswith(("POSTGRES_USER=", "POSTGRES_DB=", "POSTGRES_PASSWORD="))))' > "$backup/database.env"
user=$(sed -n 's/^POSTGRES_USER=//p' "$backup/database.env")
database=$(sed -n 's/^POSTGRES_DB=//p' "$backup/database.env")
[[ -n "$user" && -n "$database" ]]
source_image=$(docker inspect -f '{{.Config.Image}}' immich_postgres)
printf 'source=%s\nold=%s\nstage=%s\nsource_image=%s\ntarget_image=%s\n' "$source_dir" "$old" "$stage" "$source_image" "$target_image" > "$backup/paths.txt"
server_stopped=false
source_moved=false
apps_started=false
cleanup() {
  result=$?
  trap - EXIT
  docker rm -f "$temporary" >/dev/null 2>&1 || true
  rm -f "$backup/database.env"
  if (( result != 0 )); then
    restore_ok=true
    if [[ "$source_moved" = true && "$apps_started" = false ]]; then
      docker stop immich_postgres >/dev/null 2>&1 || restore_ok=false
      if [[ -d "$source_dir" ]]; then mv "$source_dir" "${source_dir}.pg16-failed-$stamp" || restore_ok=false; fi
      if [[ "$restore_ok" = true ]]; then
        mv "$old" "$source_dir" && cp -p "$backup/docker-compose.yml" docker-compose.yml \
          && "${compose[@]}" up -d --no-deps immich-database || restore_ok=false
      fi
    fi
    if [[ "$server_stopped" = true && "$apps_started" = false && "$restore_ok" = true ]]; then
      docker start immich_postgres >/dev/null && ready immich_postgres \
        && docker start immich_server >/dev/null || true
    fi
    echo "Migration failed. Recovery artifacts: $backup; inspect paths.txt." >&2
    if [[ "$apps_started" = true ]]; then
      echo 'Application writes may have reached PG16; automatic rollback was skipped.' >&2
    fi
  fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
ready() {
  local container=$1
  for (( attempt=0; attempt<120; attempt++ )); do
    if docker exec "$container" pg_isready -h 127.0.0.1 -U "$user" -d "$database" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}
counts() {
  docker exec -i "$1" psql -X -U "$user" -d "$database" -At -v ON_ERROR_STOP=1 <<'SQL' | sort
SELECT format('SELECT %L, count(*) FROM %I.%I;', table_name, table_schema, table_name)
FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name
\gexec
SQL
}
docker pull "$target_image"
docker run -d --name "$temporary" --network none --env-file "$backup/database.env" \
  --mount "type=bind,src=$stage,dst=/var/lib/postgresql/data" \
  -e POSTGRES_INITDB_ARGS=--data-checksums "$target_image" >/dev/null
ready "$temporary"
server_stopped=true
docker stop immich_server >/dev/null
clients=$(docker exec immich_postgres psql -X -U "$user" -d "$database" -Atc "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend';")
[[ "$clients" = 0 ]] || { echo 'Other database clients remain connected; refusing migration' >&2; exit 1; }
docker exec immich_postgres pg_dump -U "$user" -d "$database" --clean --if-exists | gzip > "$backup/database.sql.gz"
gzip -t "$backup/database.sql.gz"
counts immich_postgres > "$backup/source-counts.txt"
[[ -s "$backup/source-counts.txt" ]]
# Apply Immich's documented search_path adjustment for extension indexes.
gzip -dc "$backup/database.sql.gz" \
  | sed "s/SELECT pg_catalog.set_config('search_path', '', false);/SELECT pg_catalog.set_config('search_path', 'public, pg_catalog', true);/g" \
  | docker exec -i "$temporary" psql -X -U "$user" -d "$database" --single-transaction -v ON_ERROR_STOP=1
counts "$temporary" > "$backup/target-counts.txt"
diff -u "$backup/source-counts.txt" "$backup/target-counts.txt"
docker exec "$temporary" psql -X -U "$user" -d "$database" -v ON_ERROR_STOP=1 -c ANALYZE >/dev/null
docker stop "$temporary" >/dev/null
docker rm "$temporary" >/dev/null
docker stop immich_postgres >/dev/null
mv "$source_dir" "$old"
source_moved=true
mv "$stage" "$source_dir"
cp -p "$backup/compose-pg16.yml" docker-compose.yml
"${compose[@]}" up -d --no-deps immich-database
ready immich_postgres
[[ "$(docker exec immich_postgres psql -X -U "$user" -d "$database" -Atc 'SHOW server_version_num;')" = 16* ]]
counts immich_postgres > "$backup/production-counts.txt"
diff -u "$backup/source-counts.txt" "$backup/production-counts.txt"
apps_started=true
docker start immich_server >/dev/null
for (( attempt=0; attempt<120; attempt++ )); do
  if [[ "$(docker inspect -f '{{.State.Health.Status}}' immich_server)" = healthy ]]; then
    touch "$backup/COMPLETE"
    echo "PostgreSQL 16 migration complete. Recovery artifacts: $backup; original PG14 cluster: $old"
    exit 0
  fi
  sleep 2
done
echo 'Immich did not become healthy after switching to PostgreSQL 16' >&2
exit 1
