#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# The provider gives API keys precedence over username/password. Use this
# project's local account even if the caller has unrelated API credentials.
unset UNIFI_API_KEY UNIFI_CLOUD_CONNECTOR

if [[ -z "${SSL_CERT_FILE:-}" && -f "${repo_root}/certs/beelink-caddy-root.crt" ]]; then
  export SSL_CERT_FILE="${repo_root}/certs/beelink-caddy-root.crt"
fi

exec op run --env-file="${repo_root}/terraform/unifi/credentials.env.refs" \
  --env-file="${repo_root}/terraform/unifi/wifi.env.refs" -- \
  terraform -chdir="${repo_root}/terraform/unifi" "$@"
