# OPNsense host monitoring in the existing Beszel hub

This is an opt-in implementation for `192.168.2.1`, with LAN `re0` and WAN
`ue0`. It installs only a native agent. The existing Beelink hub at
`192.168.2.151` remains the root `docker-compose.yml` `beszel-hub` service,
pinned to **0.21.0** with an image digest. The native agent and shared Linux
`beszel_version` both pin **0.21.0**. The Linux agent role now reconciles
existing binaries to that pin using official release checksums. Run
`just update-beszel-agents --ask-become-pass` for that separate Linux-only
update; its focused play excludes OPNsense.

The native agent was installed on the OPNsense firewall and subsequently
connected to the existing hub. Reboot persistence and optional temperature/SMART
metrics have not been verified. The focused playbook below manages only this
agent; it does not change firewall policy or network services.

## Connectivity and authentication

The agent initiates a TLS WebSocket connection to
`https://beszel.home.arpa/api/beszel/agent-connect`, through the existing Caddy
route on Beelink port 443. It verifies Caddy's certificate using an explicitly
supplied **public** CA certificate. It requires existing DNS resolution of
`beszel.home.arpa` to `192.168.2.151` from OPNsense and reachability to that
endpoint. Verify these prerequisites; this playbook does not change DNS.

`DISABLE_SSH=true` disables Beszel's embedded SSH listener, including when the
hub is unavailable. `LISTEN=127.0.0.1:45876` is an additional conservative
fallback setting. **No inbound agent port or WAN rule is required.** Do not open
45876 on LAN or WAN. Where local egress policy restricts firewall-originated
traffic, an operator must separately review permission for OPNsense to reach
Beelink TCP 443. Installation also requires HTTPS access to GitHub release
assets and their redirects from the appliance. No firewall rules are managed.

A dedicated agent token authenticates the outbound registration; the hub's
public ed25519 key verifies the hub in Beszel's protocol. These are unrelated to
OS SSH access. Never put the hub key in root's `authorized_keys`. No hub private
key is read, Docker command run, or hub setting changed by this role. Leave the
hub's global SSH fallback setting alone so existing Linux agents keep working.
The hub may attempt its usual fallback when this system is disconnected; there
is deliberately no corresponding agent listener.

## Operator prerequisites

1. Back up the OPNsense configuration using the normal GUI workflow and have
   working console access and a maintenance window before any future deployment.
2. Confirm OPNsense on **FreeBSD 14+ amd64**. Other architectures, generic
   FreeBSD, pfSense, and Linux fail before mutation. The appliance is detected
   through the same marker paths as the upstream installer. The deployed
   appliance reports FreeBSD 15.1 amd64; persistence across reboot remains
   unverified.
3. Establish authorized **shell access** separately. SSH was previously disabled.
   If needed, an operator must enable it in the OPNsense GUI, limit it to LAN
   and the administration source, configure a dedicated firewall shell account
   and key through OPNsense's user management, and verify its host key. Do not
   reuse AP credentials. The OPNsense API key/secret in 1Password are **not**
   SSH credentials and cannot substitute for shell access. This role does not
   enable SSH, create login users, or edit GUI authentication or permissions.
4. The chosen shell account must already have an authorized path to root for
   Ansible (`become: true`). For a non-root user, arrange supported sudo access
   separately; use `--ask-become-pass` if needed. Alternatively configure an
   already approved `su` become method and enter its password interactively.
   Direct root SSH is not required. Do not add blanket permissions to the
   existing `daemon` user. Decide separately whether to disable bootstrap SSH
   again after verification; future Ansible runs require authorized shell access.
5. Confirm a working target Python 3 interpreter (example:
   `/usr/local/bin/python3`), `/bin/sh`, native `tar`, `daemon`, `service`,
   `getent`, and the stock `daemon` account/group. The role installs no Python,
   packages, kernel modules, sudo rules, or users. Never use generic FreeBSD
   repositories to bootstrap packages on OPNsense for this role.
6. On the controller, install the repository Ansible collections, Python's
   Ansible dependencies, OpenSSL, and the 1Password CLI. Authenticate `op` using
   its normal desktop integration/sign-in. The controller reads the token with
   `op read` under `no_log`, with privilege escalation disabled locally.

## Register the system and prepare local configuration

Use the **existing** hub at `https://beszel.home.arpa`. In **Add System**, create
`OPNsense` with Host/IP `192.168.2.1` and port `45876` (metadata for the hub's
fallback; do not open the port). Save the system and use the generated
**system-specific** token from its installation configuration, rather than a
shared universal token. Store it in the dedicated 1Password item/field
`op://Homelab/OPNSense Townhaus/beszel_token`. The role reads the secret value
on the controller with `op read`, suppresses task output, and writes it only to
the root-owned mode-0600 agent environment. Copy the displayed public
`ssh-ed25519 ...` hub key to the ignored host vars file. Do not paste tokens
into shell commands, Git, chat, Ansible extra vars, or logs.

Prepare the standalone inventory and local host vars:

```sh
umask 077
cp ansible/inventory.opnsense.ini.example ansible/inventory.opnsense.ini
cp ansible/host_vars/opnsense_firewall.yml.example ansible/host_vars/opnsense_firewall.yml
chmod 600 ansible/inventory.opnsense.ini ansible/host_vars/opnsense_firewall.yml
```

Edit the inventory's SSH username, verified Python path, and any necessary
become settings. The group-specific `ansible/group_vars/opnsense.yml` overrides
the repository's Linux `/bin/bash` default with `/bin/sh` before fact gathering
and selects a private per-login-user temporary directory under `/tmp`.
INI group variables alone cannot override the YAML `group_vars/all` defaults.
Keep the inventory hostname `opnsense_firewall`
so Ansible loads the example host vars. Do not put this appliance in
`townhaus_caddy`, `aswitch`, or `pi_cam`. The general `just deploy` recipes do
not include this playbook. Use the standalone inventory for all firewall work;
existing broad `ansible all` Linux maintenance commands must not target it.

Obtain Beelink Caddy's **public CA certificate only** over an already trusted
administrative connection. For example, these read-only Beelink commands match
the current root Compose service; run them only if that access is available:

```sh
mkdir -p certs
ssh beelink 'cd /srv/docker/townhaus-caddy && docker compose exec -T caddy cat /data/caddy/pki/authorities/local/root.crt' > certs/beelink-caddy-root.crt
openssl x509 -in certs/beelink-caddy-root.crt -noout -subject -fingerprint -sha256
```

Compare that fingerprint through a trusted channel to Beelink's Caddy CA.
Never copy `root.key`, the hub private key, or an arbitrary certificate offered
by an unverified TLS endpoint. The ignored `certs/` path in the example host
vars resolves relative to this repository. Set `opnsense_beszel_agent_ca_file` to
another controller-local public PEM if appropriate. No global trust store is
modified. Verify the firewall clock, DNS, and certificate hostname before use;
TLS verification is never bypassed.

## Checks and focused deployment

These checks run locally without contacting a host:

```sh
just test-opnsense-beszel
ansible-playbook --syntax-check -i ansible/inventory.opnsense.ini.example ansible/playbooks/opnsense_beszel.yml
just lint
git diff --check
```

The focused commands for deployment, preview, and repeat-run verification are:

```sh
# Check SSH/become/Python and platform facts first; no bootstrap is performed.
ansible opnsense -i ansible/inventory.opnsense.ini -b --ask-become-pass -m ansible.builtin.setup -a 'filter=ansible_system,ansible_architecture,ansible_kernel'
# Preview changes; this still reads the token locally under no_log.
just configure-opnsense-beszel --check --ask-become-pass
# Apply only the native firewall agent.
just configure-opnsense-beszel --ask-become-pass
# Repeat to verify changed=0 when version/configuration and service state agree.
just configure-opnsense-beszel --ask-become-pass
```

The recipe is equivalent to
`ansible-playbook -i ansible/inventory.opnsense.ini ansible/playbooks/opnsense_beszel.yml`.
Omit `--ask-become-pass` only when the approved access method does not need it.
On a fresh host, check mode cannot fully simulate creating directories or
installing a missing binary; it skips binary installation and service changes.
An initial preview can fail on a missing destination directory. Syntax and
offline tests do not require live access.

## Managed files and lifecycle

| Path | Purpose / permissions |
| --- | --- |
| `/usr/local/sbin/beszel-agent` | Root-owned 0755 binary, atomically copied from verified staging |
| `/var/cache/beszel-agent/0.21.0/` | Root-only versioned release archive and staging directory |
| `/usr/local/etc/beszel-agent/env` | Root:wheel 0600 environment, token included; no template diff/log output |
| `/usr/local/etc/beszel-agent/hub-ca.crt` | Public CA, root-owned 0644 |
| `/usr/local/etc/rc.d/beszel-agent` | Root-owned 0755 native rc service |
| `/etc/rc.conf.d/beszel_agent` | Dedicated enable flag; does not rewrite `/etc/rc.conf` |
| `/usr/local/etc/rc.syshook.d/start/99-beszel-agent` | Root-owned 0755 hook respecting enabled/running checks |
| `/var/db/beszel-agent` | daemon:daemon 0700 persistent fingerprint/state |
| `/var/run/beszel-agent` | daemon:daemon 0700 runtime directory, recreated at start; private health file |
| `/var/run/beszel_agent.pid` | Supervisor PID; child PID uses `.child.pid` |
| `/var/log/beszel_agent.log` | Root-owned 0600 log |
| `/usr/local/etc/newsyslog.conf.d/beszel-agent.conf` | Rotate at 256 KiB, retain three compressed logs, signal supervisor to reopen |

The root rc prestart reads and exports the private environment before
`daemon -u daemon` drops the **child's** privilege. Credentials are never passed
as process arguments. The supervisor remains root and restarts an exited child
after five seconds; stopping the supervisor also stops the child and prevents
respawning. Native rc PID checks handle start/status/stop. Start fails closed if
the binary or environment is missing. Boot does not run `op`, download software,
or depend on an unlocked controller. The guarded syshook avoids a duplicate
start if OPNsense also starts the enabled rc service.

OPNsense regenerates users from its configuration; reusing the stock `daemon`
account follows the current upstream installer and avoids an unmanaged user
disappearing at reboot. That account is shared with other local daemons; it is
not a dedicated security boundary against those processes. Only this agent's
state/runtime directories are assigned to it. Binaries, scripts, environment,
and logs remain root-owned. Shell access is needed only for management, not for
normal metric transport. Do not run with debug logs or dump environments/facts
containing tokens. Use default callbacks, no fact cache of credentials, and
normal Ansible private temporary directories; never enable world-readable
temporary-file fallbacks.

The rc script clears stale installer `beszel_agent_user`, group, flags, program,
and env-file overrides after loading the enable flag, so an old
`beszel_agent_user=daemon` cannot run the supervisor without access to its PID
and log files. Configure the launch through this role rather than rc overrides.

Version/config/CA/binary changes notify a single agent restart; an unchanged run
does not restart it. A stopped enabled agent is started again. To update, review
the matching hub and agent releases, add the reviewed archive SHA-256 to
`opnsense_beszel_agent_release_sha256`, and change `opnsense_beszel_agent_version`. Unknown
versions fail. The download checks the committed digest on every apply; there
is no `curl | sh`, cron updater, or `beszel-agent update` hook. Old root-only
staging directories remain available for a deliberate rollback. To roll back,
restore the prior reviewed version/checksum and rerun the focused playbook.

To disable after authorization, set `opnsense_beszel_agent_enabled: false` in the
ignored host vars and run the same focused recipe. It writes `NO` if installed,
stops the agent, removes the boot hook and environment (including token), and
retains the binary, CA, logs, and state. A second disabled run is unchanged;
disabling a never-installed agent does not install files. Disabled runs do not
read 1Password or download releases. Re-enable by setting `true` and rerunning
with the token reference and verified CA available. Revoke the system token in
the hub separately if retiring the monitor; disable alone does not revoke it.

## Verification, upgrades, and recovery

After an authorized apply, run these read-only commands in an authorized root
shell on OPNsense (or through the chosen privilege escalation method):

```sh
uname -srm
/usr/local/sbin/beszel-agent --version
service beszel-agent enabled
service beszel-agent status
env TMPDIR=/var/run/beszel-agent /usr/local/sbin/beszel-agent health
sockstat -46 -l | awk 'NR == 1 || /beszel/'
sockstat -46 -c | awk 'NR == 1 || /beszel/'
df -h /
netstat -ibdn
sysctl dev.cpu.0.temperature
tail -n 40 /var/log/beszel_agent.log
```

Expect version 0.21.0, an enabled/running supervisor, no agent listening socket,
and an outbound connection to Beelink:443. The health command checks a recently
touched health file; it does not prove accurate metrics or successful hub
authentication by itself. Check the existing hub for `OPNsense` becoming online,
increasing sample timestamps, CPU/RAM/root filesystem data, and separate `re0`
and `ue0` traffic. Compare with the firewall GUI and `df`/`netstat`; allow several
sampling intervals. Missing temperature sysctls are expected on some hardware.
Keep logs local/redact before sharing. Do not print the environment or token.

Custom binaries, rc files, hooks, CA, and state are **not an OPNsense plugin**
and are not covered by a normal `config.xml` backup. They may survive ordinary
reboots and some updates, but firmware reinstall, major upgrades, `/var` RAM
disks, or recovery can remove them. Before an upgrade, retain the inventory,
public key, verified public CA, and 1Password reference securely, and back up
state separately if preserving the fingerprint matters. No local credential
file or CA restoration from `config.xml` is promised. Do not add an automatic
post-upgrade root installer to compensate.

After a separately authorized reboot/upgrade, repeat the checks above. If files
are missing, rerun the focused playbook after checking the new FreeBSD version
and obtaining deployment authorization. If files exist but the agent is offline,
check the enable flag, supervisor/child, DNS, time, TLS CA/hostname, token status,
and hub logs first. The configuration in `/etc/rc.conf.d/beszel_agent` takes
precedence over `/etc/rc.conf`; check for conflicting third-party configuration.
For an authorized manual service recovery use `service beszel-agent restart`,
then verify it; do not restart networking or the firewall. Do not reset a changed
fingerprint in the hub until its cause has been checked. A reinstall may require
an operator to approve/re-register the replacement fingerprint in the same hub.

## Metrics and limitations

- CPU, RAM, root filesystem capacity/usage, and interface counters use native
  FreeBSD host collectors. `NICS=re0,ue0` selects the known LAN/WAN interfaces;
  throughput is from counter deltas, not packet inspection. Summing LAN and WAN
  can count routed traffic twice. There are no per-client, DHCP, DNS, firewall
  rule, gateway, or flow metrics.
- `FILESYSTEM=/` selects the root mount. Set `opnsense_beszel_agent_filesystem` or
  `opnsense_beszel_agent_extra_filesystems` after inspecting actual UFS/ZFS mounts.
  Disk I/O, pools, datasets, and memory accounting can differ from Linux or the
  OPNsense GUI, especially ZFS ARC; do not assume every Linux chart is available.
- In 0.21.0, temperatures come from readable `dev.cpu.N.temperature` and
  `hw.acpi.thermal.tzN.temperature` sysctls. Missing drivers/sensors produce no
  temperatures. This role loads no modules and changes no sysctl settings.
- SMART requires an existing compatible `smartctl` (7+) and device access for
  `daemon`; the FreeBSD binary does not embed smartctl. If supported by existing
  permissions/hardware it may report data; otherwise expect no SMART metrics.
  This implementation adds no disk groups, devfs rules, broad sudo privileges,
  packages, or root agent mode just to obtain optional SMART data.

## Research and offline test coverage

Primary sources checked during implementation (2026-11-07):

- [Current official installer](https://github.com/henrygd/beszel/blob/main/supplemental/scripts/install-agent.sh):
  explicit OPNsense detection, existing `daemon` user, native rc service and
  `/usr/local/etc/rc.syshook.d/start/99-beszel-agent` boot persistence. This role
  deliberately exports its environment explicitly and respects the enable flag
  in the hook rather than unconditionally using `onestart`.
- [Pinned 0.21.0 release assets](https://github.com/henrygd/beszel/releases/expanded_assets/v0.21.0)
  and [checksum manifest](https://github.com/henrygd/beszel/releases/download/v0.21.0/beszel_0.21.0_checksums.txt):
  downloaded FreeBSD amd64 archive independently matched
  `2c3a06ef4955e9da5b18439eabac0b2101fe096fed8adc94473ccec80a512e29`.
- [Pinned client/auth/TLS code](https://github.com/henrygd/beszel/blob/v0.21.0/agent/client.go),
  [listener guard](https://github.com/henrygd/beszel/blob/v0.21.0/agent/server.go),
  [FreeBSD sensors](https://github.com/henrygd/beszel/blob/v0.21.0/agent/sensors_freebsd_common.go),
  [SMART collector](https://github.com/henrygd/beszel/blob/v0.21.0/agent/smart.go), and
  [non-Windows smartctl behavior](https://github.com/henrygd/beszel/blob/v0.21.0/agent/smart_nonwindows.go).
- [Beszel configuration](https://beszel.dev/guide/environment-variables),
  [agent registration](https://beszel.dev/guide/agent-installation), and
  [SMART requirements](https://beszel.dev/guide/smart-data).
- [OPNsense boot lifecycle](https://docs.opnsense.org/development/backend/autorun.html)
  and [newsyslog include paths](https://github.com/opnsense/core/blob/master/src/opnsense/service/templates/OPNsense/Syslog/newsyslog.conf),
  plus [FreeBSD daemon implementation](https://github.com/freebsd/freebsd-src/blob/releng/14.3/usr.sbin/daemon/daemon.c).
- [Historical OPNsense discussion](https://github.com/henrygd/beszel/discussions/39)
  supplies context; old user-creation and startup snippets are not deployment
  instructions for this role.

`scripts/test-opnsense-beszel.py` executes the real role in a temporary local
filesystem, replacing downloads with a locally generated archive, account
lookups with fixture assertions, and service operations with a stateful fake.
Ownership is omitted only inside that fixture. It tests installation/repeat-run
idempotency, update/config restarts, disable/re-enable, checksum rejection,
unsupported platforms/releases, Linux role guards, protected rendered config,
shell quoting, and repeated/disabled boot-hook events. It also runs the rc
prestart with a stub rc library. These are offline contract/orchestration tests;
actual FreeBSD rc/daemon signal behavior, host metrics, installation, TLS
connectivity, upgrade survival, and reboot persistence still require an
authorized appliance test.
