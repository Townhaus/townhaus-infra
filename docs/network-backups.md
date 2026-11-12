# Network configuration backups

Run from the repository root:

```sh
bash scripts/network-backup.sh --opnsense-insecure
```

The wrapper loads only the existing OPNsense API and UniFi local admin
credentials through `op run`; it does not need Wi-Fi password fields. It downloads:

- OPNsense's latest saved configuration XML from
  `GET /api/core/backup/download/this`. The XML must parse as an `opnsense` root
  containing system and interface configuration. Current OPNsense saves history
  after writing each new configuration, so the latest saved revision includes
  applied changes.
- A full UniFi OS configuration backup from `GET /api/backup/download`, after
  local cookie authentication. This includes application configuration; it is
  not a Debian disk image, Docker backup, or a substitute for server backups.

Files are saved on the machine running the command under
`backups/network/<UTC timestamp>/`:

```text
opnsense-config.xml
unifi-system.unifi
manifest.json
SHA256SUMS
```

The backup tree is ignored by Git. New run directories have mode 0700 and files
0600. Backups contain private keys, credentials and Wi-Fi configuration; do not
attach or commit them. These local files are not additionally encrypted by the
script. Copy completed backups to protected storage on another machine for
recovery from host/storage loss. No automatic deletion or retention policy runs.

The manifest records byte counts, SHA-256 and per-download status. Partial files
are removed on failure; successful backups survive a failure of the other target.
The command exits nonzero if any requested backup fails. HTTP bodies, credentials
and backup contents are never printed. No restore operations or controller
configuration changes are performed. UniFi may generate a temporary backup as
part of download.

## TLS and access

UniFi automatically uses `certs/beelink-caddy-root.crt` when present. Use
`--unifi-ca-file PATH` to select a different trusted CA. Router TLS is verified
by default; `--opnsense-insecure` is the explicit exception for the current local
bootstrap setup. Replace it with `--opnsense-ca-file PATH` once the correct
router issuer certificate is exported/trusted. The two servers can use different
CAs. Redirects are rejected so credentials are not sent to a different origin.

The OPNsense API user needs backup/configuration-history download access. A
working VLAN API key does not guarantee that privilege. UniFi full OS backup
may require console-level admin privileges beyond Network management. A 401/403
is reported without printing the response body. Use a suitably scoped backup
account if required and update the 1Password references deliberately.

## Other backup scopes

```sh
# Both controllers, plus an additional settings-only Network .unf export:
bash scripts/network-backup.sh --opnsense-insecure --unifi-scope both

# Network-only UniFi export when console backup access is unavailable:
bash scripts/network-backup.sh --only unifi --unifi-scope network

# OPNsense only:
bash scripts/network-backup.sh --only opnsense --opnsense-insecure
```

Network exports use the local undocumented Network API and request zero days
of historical events. The script requires an expected same-origin backup path
and downloads the returned archive; it never follows a server-supplied external
URL. System and Network scopes are recorded separately so a Network-only backup
is not mistaken for a full OS configuration backup.

## Verification and limits

From a completed backup directory:

```sh
shasum -a 256 -c SHA256SUMS
```

OPNsense XML is structurally checked. UniFi backups can be encrypted/proprietary:
the script verifies transfer length, nonempty archive-sized data and rejection
of HTML/JSON error responses, not archive decryptability. The manifest explicitly
records `restore_tested: false`. Checksums detect subsequent corruption but do
not prove restorability. A separate restore test on a spare instance is needed
for that assurance.

Offline checks (no controller or 1Password access):

```sh
python3 scripts/test-network-backup.py
```

The OPNsense download uses Curl by default, with authentication supplied through
stdin configuration rather than process arguments. Curl ignores its user config
and does not follow redirects. This accommodates the observed macOS session
where Python could reach UniFi but reported no route to the router while Curl
could reach it. `--opnsense-transport urllib` selects the Python-only transport
on hosts where it works. UniFi uses Python's cookie session transport.

## Scheduling

This setup is an on-demand command; no cron, launchd or systemd job is installed.
Normal 1Password desktop sessions may prompt for approval and are not a reliable
unattended credential source. Before scheduling, choose a backup destination,
retention policy and unattended 1Password Connect/service-account arrangement.
The CLI accepts `--output-dir PATH` for a protected external backup destination.

References:

- https://docs.opnsense.org/development/api/core/core.html
- https://github.com/opnsense/core/blob/stable/26.7/src/opnsense/mvc/app/controllers/OPNsense/Core/Api/BackupController.php
- https://github.com/brettmiller/unifi-backup (observed UniFi local backup endpoints)
