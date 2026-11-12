#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# OPNsense's certificate issuer may differ from Caddy's; do not automatically
# reuse the UniFi CA certificate or silently disable TLS verification.
exec op run --env-file="${repo_root}/terraform/opnsense/credentials.env.refs" -- \
  terraform -chdir="${repo_root}/terraform/opnsense" "$@"
