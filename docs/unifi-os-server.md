# UniFi OS Server on the Beelink

The standalone playbook installs Ubiquiti's official Linux x64 installer on
Debian 13 or later. It runs alongside the existing Docker stack using the
installer's Podman/systemd setup. It does not change Docker, Caddy, DNS, firewall
rules, Wi-Fi settings, or VLANs, and does not migrate or stop the HA controller.

## Pin the installer

Get the Linux x64 UniFi OS Server URL and SHA-256 from the official release linked
at <https://ui.com/download/unifi>. Use a stable release, not the legacy Network
Server package. Record both values in a source-controlled vars file:

```sh
cp ansible/unifi-os-server.vars.yml.example ansible/unifi-os-server.vars.yml
# Edit the new file and fill in the URL and SHA-256.
```

These values are not secrets. Keep the versioned URL and checksum in Git for
reproducibility. Do not commit controller backups, credentials, or Terraform state.

## Deploy

Download a fresh backup from the existing HA controller before proceeding.
Reserve 192.168.2.151 for the Beelink in OPNsense. Ubiquiti's migration guide
instructs stopping the old Network Server before installing OS Server; stop the
UniFi HA app and disable its automatic restart/watchdog for the migration window.
The APs and switches continue using their last configuration while it is stopped.

From the repository root:

```sh
ansible-playbook ansible/playbooks/unifi_os_server.yml \
  --limit beelink.local --ask-become-pass \
  -e @ansible/unifi-os-server.vars.yml --check --diff

ansible-playbook ansible/playbooks/unifi_os_server.yml \
  --limit beelink.local --ask-become-pass \
  -e @ansible/unifi-os-server.vars.yml
```

Check mode validates inputs and previews packages; it does not download or run
the installer, check port availability, or verify a future installation.
Initial installation checks TCP 8080 and 11443. Ensure UDP 3478 and 10001 are also
available and reachable from the devices. The web interface is expected at
<https://192.168.2.151:11443>; Caddy can continue using 80/443. No WAN port
forwarding is needed for local management.

The playbook skips installation when uosserver.service exists, so subsequent
runs ensure the service is enabled and started without reinstalling or
downgrading it. Changing the pinned URL does not upgrade an existing installation.
Use UniFi's update interface for upgrades. This is an install/bootstrap playbook,
not an exact-version reconciler. A partial installation that left a unit behind
needs inspection rather than an automatic reinstall.

The official installer runs with `--non-interactive` and a 20-minute limit.
Ansible polls the installer job every five seconds. Output is returned when the
job finishes; the polling messages do not indicate individual installation steps.

## Migrate and verify

1. Complete OS Server setup and update its Network application to **10.6.109 or
   newer** before importing the current controller configuration.
2. Use UniFi's site migration/export workflow to transfer configuration and point
   the devices at the Beelink's inform address, `http://192.168.2.151:8080/inform`.
   Restoring configuration alone may leave devices pointing at the old HA IP.
3. Confirm both switches and both APs are online, and test the existing SSIDs.
4. Update any Home Assistant UniFi integration to the new controller.
5. Keep the old app stopped and retain its backup until migration is verified.

Do not reset or forget devices just to migrate them. If installation fails,
inspect `sudo journalctl -u uosserver -n 100 --no-pager`. Before any devices have
been moved, restarting the old HA app restores the previous management service.
After moving devices, rollback also requires pointing their inform addresses
back to the old controller.

Wi-Fi, VLANs, and switch settings are a separate future OpenTofu configuration;
this playbook only manages the controller host installation.

## Admin hostname

`unifi-admin.home.arpa` resolves through AdGuard to `192.168.2.151`. Caddy
terminates HTTPS using the existing internal CA and proxies to the UniFi OS
Server on `https://192.168.2.151:11443`. The previous `unifi.home.arpa` alias is
also served. The short `unifi` DNS record points to the Beelink for controller
discovery on port 8080. Home Assistant remains at `192.168.2.91`.

Apply DNS rewrites, Caddy configuration, and the portal link without redeploying
the rest of the Docker stack:

```sh
ansible-playbook ansible/playbooks/unifi_admin.yml \
  --limit beelink.local --ask-become-pass
```

This uses the existing local AdGuard credential references in
`ansible/group_vars/townhaus_caddy/beszel.yml` and requires the 1Password CLI to be
signed in. Caddy is briefly recreated when its configuration changes, to refresh
its file bind mount. Other containers are not recreated. Clients must trust the
existing Caddy internal CA, just as for the other internal services.

References:

- <https://help.ui.com/hc/en-us/articles/34210126298775-Self-Hosting-UniFi>
- <https://help.ui.com/hc/en-us/articles/218506997-Required-Ports-Reference>
