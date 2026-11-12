#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "${repo_root}"
exec op run --env-file="${repo_root}/terraform/opnsense/credentials.env.refs" -- \
  ansible-playbook ansible/playbooks/opnsense_dhcp.yml "$@"
