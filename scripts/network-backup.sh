#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
umask 077
exec op run \
  --env-file="${repo_root}/terraform/opnsense/credentials.env.refs" \
  --env-file="${repo_root}/terraform/unifi/credentials.env.refs" -- \
  python3 "${repo_root}/scripts/network-backup.py" "$@"
