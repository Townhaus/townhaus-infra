# UniFi network configuration

Terraform manages UniFi network definitions and SSIDs. Ansible installs the
controller. OPNsense owns VLAN interfaces, subnets, DHCP, DNS and firewall rules.
There is no OPNsense provider configured here yet. Switch port overrides and AP
radio settings are also deferred until the physical topology is confirmed.

Known working baseline:

| Item | Value |
| --- | --- |
| Controller | UniFi OS Server on Beelink, 192.168.2.151 |
| Admin URL | https://unifi-admin.home.arpa |
| Network application | 10.6.109 |
| Existing Home SSID | GREATWHITE |
| Home Assistant | 192.168.2.91 |
| Devices | Two USW Flex 2.5G 5 switches and two U7 Lite APs |
| OPNsense | 26.7.8_2 |

Discovery confirmed the Home LAN as 192.168.2.0/24 (gateway 192.168.2.1).
OPNsense LAN is `ue1` (192.168.2.1/24); WAN is `ue0`. The user confirmed
192.168.20.0/24 and 192.168.30.0/24 are unused. IoT will use VLAN 20 with router
192.168.20.1/24; Guest will use VLAN 30 with router 192.168.30.1/24.

| Switch IP | Port | Connection |
| --- | --- | --- |
| 192.168.2.214 | 2 | Mac M4 Mini |
| 192.168.2.214 | 3 | Switch 192.168.2.190 port 5 |
| 192.168.2.214 | 4 | Third-floor U7 Lite |
| 192.168.2.214 | 5 | OPNsense ue1 |
| 192.168.2.190 | 1 | Backyard camera |
| 192.168.2.190 | 2 | Home Assistant 192.168.2.91 |
| 192.168.2.190 | 3 | First-floor U7 Lite |
| 192.168.2.190 | 4 | Beelink 192.168.2.151 |
| 192.168.2.190 | 5 | Switch 192.168.2.214 port 3 |

The router link, inter-switch link and AP ports need tagged VLANs 20 and 30
alongside the existing untagged/native Home network. Host ports remain on Home
until a deliberate migration; camera port 1 can move to IoT after testing.

## Bootstrap

Use Terraform 1.11 or newer. The provider is pinned to 0.65.0; commit
`.terraform.lock.hcl`. GREATWHITE is defined in `home.tf`. VLANs 20 and 30 are now enabled in the
checked-in `vlan_networks` defaults, matching the applied OPNsense configuration.
The additional `wifi_networks` defaults now define GREATWHITE-IoT and
GREATWHITE-Guest:

```sh
cd terraform/unifi
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform validate
```

Create a dedicated local Terraform user in UniFi with Network management
permissions. The configured 1Password item is `Homelab/Unifi Local Admin`, using
its `username` and `password` fields. `credentials.env.refs` contains references
only. From the repository root, run authenticated commands through:

```sh
bash scripts/unifi-terraform.sh plan
# Only after reviewing an import-only plan:
bash scripts/unifi-terraform.sh apply
```

The wrapper uses `op run` to inject `UNIFI_USERNAME` and `UNIFI_PASSWORD` only
into the Terraform process and masks resolved values in its output. Sign in to
1Password CLI or enable its desktop app integration first. Do not enable shell
tracing or Terraform debug logs while using credentials. Do not use a cloud-only
or MFA account. The provider also accepts `UNIFI_API_KEY`, but compatibility with
the required resource endpoints must be verified; a working web login alone
does not verify provider access. Never paste credentials into chat.

Trust Caddy's internal root CA in the environment running Terraform. If needed
for bootstrap, explicitly set `allow_insecure = true` in ignored local tfvars,
or use the direct `https://192.168.2.151:11443` URL with that option. Verify the
internal site identifier after migration rather than assuming the display name
is the site ID. Discovery confirmed `default` for Ballard Townhouse.
The wrapper automatically uses `certs/beelink-caddy-root.crt` if present, unless
`SSL_CERT_FILE` is already set.

## Import GREATWHITE first

First verify authentication and retrieve a filtered inventory. From the repo root:

```sh
op run --env-file=terraform/unifi/credentials.env.refs -- python3 scripts/unifi-discover.py
```

This logs in and makes read-only inventory requests; it does not modify network
settings. Output uses an explicit field allowlist and excludes Wi-Fi passwords,
tokens and session cookies. If Python does not trust Caddy's CA, add
`--ca-file /path/to/caddy-root.crt`. If the migrated site is not `default`, the
command lists the available site identifiers; rerun with `--site IDENTIFIER`.

The confirmed inventory is now represented in `home.tf` as `unifi_wlan.home`,
with an import block for WLAN ID `5d8fd52e9cd9a20667736268`. It explicitly retains
WPA2/CCMP, both radio bands, the current AP group, and current roaming/isolation
settings. The existing Home network is referenced by ID, not recreated.

Run from the repo root:

```sh
bash scripts/unifi-terraform.sh plan
```

The desired first plan is **1 to import, 0 to add, 0 to change, 0 to destroy**.
If it proposes any update, inspect it before applying and match remaining
controller settings in the HCL. Do not apply unexpected security, radio or AP
assignment changes. Once the plan contains only the import, use:

```sh
bash scripts/unifi-terraform.sh apply
bash scripts/unifi-terraform.sh plan
```

The apply prompts for approval; review its plan again. It should only record the
existing WLAN in state. The subsequent plan should show no changes.

Home password management is deliberately excluded using `ignore_changes` on
`passphrase`. No Wi-Fi password input is required for adoption. Import may still
read the current passphrase into local state; treat state/backups as confidential.
Ignore rules hide changes in plans but do not redact stored data.

The two additional SSIDs use the `wifi_networks` map and separate ephemeral
`iot_wifi_password` / `guest_wifi_password` variables with write-only provider
fields. The wrapper injects them from `wifi.env.refs`. Increment the relevant
SSID's `password_version` when rotating its password. Do not put GREATWHITE in that map as well: it is managed
by `unifi_wlan.home`. Keep local state outside Git and back it up securely. For
shared/CI use, select a protected remote backend first.

## Add VLANs and new SSIDs

1. Confirmed: parent `ue1`, Home 192.168.2.0/24, unused new subnets, topology above.
2. Create OPNsense VLAN 20 and 30 interfaces, addresses, DHCP and firewall policy.
   Guest should have internet only; IoT should have explicit access rules for
   Home Assistant. Account for both IPv4 and IPv6. Permit DNS to the selected
   resolver; if using Beelink AdGuard, allow TCP/UDP 53 without opening other LAN
   access. Home Assistant discovery/casting may require selective mDNS handling.
3. Add matching UniFi VLAN-only networks using the example. If they already
   exist, import with `terraform import 'unifi_network.vlan["iot"]' NETWORK_ID`.
4. Configure the known switch/AP uplinks to carry these VLAN tags while preserving
   the existing native management LAN. Do not invent port assignments.
5. Add IoT and Guest entries to `wifi_networks`, using `vlan_key = "iot"` or
   `"guest"`, their actual SSID names and user group IDs. Supply matching password
   keys. Guest can use `client_isolation = true`; gateway firewall policy still
   provides the actual routed isolation.
6. Review `terraform plan`, then apply only the understood changes. Test one
   client per SSID for addressing, DNS, internet and intended isolation.

`prevent_destroy` blocks deletion/replacement while resource blocks remain in
configuration, including removed map keys. Deleting the entire resource block
also removes that protection. It does not block disruptive in-place changes.
Always review the plan. Do not run `terraform destroy` against the home network.

Provider documentation:

- https://registry.terraform.io/providers/ubiquiti-community/unifi/0.65.0/docs
- https://github.com/ubiquiti-community/terraform-provider-unifi/blob/v0.65.0/docs/resources/network.md
- https://github.com/ubiquiti-community/terraform-provider-unifi/blob/v0.65.0/docs/resources/wlan.md

## Current rollout stage

The controller migration, OPNsense VLANs/assignments/DHCP/firewall, UniFi VLANs
and both new SSIDs are applied. The user confirmed client tests look good and
both Terraform roots have clean plans. Existing GREATWHITE remains unchanged.
Automatic source NAT explicitly includes IoT and Guest.

From the repository root, verify the live configuration with:

```sh
bash scripts/unifi-terraform.sh plan
bash scripts/opnsense-terraform.sh plan -var='allow_insecure=true'
```

Both should report no changes. If an older local `terraform.tfvars` sets
`vlan_networks = {}` or `wifi_networks = {}`, remove that override to use the
checked-in definitions.

The switch inventory showed `forward: all` on every port, no overrides and no
port profiles. Native Home management was preserved; no switch port changes
were required. Read the current port configuration if troubleshooting:

```sh
op run --env-file=terraform/unifi/credentials.env.refs -- \
  python3 scripts/unifi-discover.py --ca-file certs/beelink-caddy-root.crt --switches
```

The output uses allowlisted fields. Migrate remaining devices in batches and
add only the required discovery/callback exceptions. Current API backups are
available through [the network backup command](../../docs/network-backups.md).

## New SSID passwords and rollout

Create an item named **UniFi WiFi** in the **Homelab** 1Password vault with two
password fields: `iot_password` and `guest_password`. Generate different strong
8–63 character passwords (20–32 random ASCII characters is suitable). These are
separate from the controller admin credentials and existing GREATWHITE password.
The tracked `wifi.env.refs` file contains references only; `op run` loads the
values directly as strings. Read-only discovery continues using only admin
credentials and does not need the new Wi-Fi secrets.

| SSID | VLAN | Security | Client isolation |
| --- | --- | --- | --- |
| GREATWHITE-IoT | 20 | WPA2/CCMP, PMF disabled for older devices | Off |
| GREATWHITE-Guest | 30 | WPA2/WPA3 transition, PMF optional | On |

Both use the existing all-AP group and 2.4/5 GHz bands. Fast roaming is off
initially. `is_guest` is false intentionally: isolation is enforced by the
OPNsense policy and Wi-Fi `l2_isolation`, without a captive portal/guest-policy
dependency on a UniFi gateway. GREATWHITE stays as imported.

Remove any older local `wifi_networks = {}` override in `terraform.tfvars` to use
the checked-in SSID defaults. After creating the password fields, run:

```sh
bash scripts/unifi-terraform.sh plan
```

During initial SSID creation, the plan added two SSIDs after the two VLANs
were applied. That rollout is complete: subsequent plans should show no changes. No update to GREATWHITE or switch management should appear. Review
the plan, then apply and verify a no-change plan. Do not migrate all devices at
once. Test each SSID for DHCP, DNS, internet, and failed router/LAN access; test
HA-initiated access to an IoT client and Guest client isolation too. AdGuard's
client access settings/host firewall must also allow DNS from the two subnets.
