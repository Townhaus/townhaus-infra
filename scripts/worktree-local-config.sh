#!/usr/bin/env bash
# Symlink the local-only files listed in .worktreeinclude from the main
# checkout into a worktree, so playbooks and Terraform work there too.
#
# Usage: scripts/worktree-local-config.sh [worktree-dir]   (default: current)
#
# Symlinks, not copies: one real copy of each secret, and edits (or Terraform
# state updates) land in the main checkout. Existing files are left alone.
set -euo pipefail

worktree="$(cd "${1:-.}" && git rev-parse --show-toplevel)"
main="$(cd "$(git -C "$worktree" rev-parse --path-format=absolute --git-common-dir)/.." && pwd)"

if [[ "$worktree" == "$main" ]]; then
  echo "This is the main checkout; nothing to link." >&2
  exit 0
fi
if [[ ! -f "$main/.worktreeinclude" ]]; then
  echo "No .worktreeinclude in $main." >&2
  exit 1
fi

linked=0 skipped=0
while IFS= read -r rel; do
  src="$main/$rel" dst="$worktree/$rel"
  if [[ -e "$dst" || -L "$dst" ]]; then
    skipped=$((skipped + 1))
    continue
  fi
  mkdir -p "$(dirname "$dst")"
  ln -s "$src" "$dst"
  echo "linked $rel"
  linked=$((linked + 1))
done < <(git -C "$main" ls-files --others --ignored --exclude-from="$main/.worktreeinclude")

echo "$linked linked, $skipped already present ($worktree)"
