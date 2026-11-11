# OPNsense Terraform

Separate state from `../unifi`, targeting OPNsense 26.7.8_2. The community
`browningluke/opnsense` provider is pinned to 0.26.0. Its maintainers describe it
as under development and not recommended for production. Review each plan and
retain the OPNsense configuration backup before applying router changes.

This configuration defines VLAN 20 (IoT) and 30 (Guest) on `ue1`, behind
switch 192.168.2.214 port 5. Both VLANs have been applied; `create_vlans` now
defaults to `true` to preserve that desired state on subsequent plans.
Home LAN and WAN are retained. Both VLAN assignments, new DHCP pools and
firewall isolation rules are applied and tested.

## Coverage and rollout

| Configuration | Provider 0.26.0 coverage |
| --- | --- |
| VLAN devices | Supported; VLANs 20 and 30 applied |
| Interface assignment and interface IP | Not available in this provider release; UI/API/Ansible step |
| Firewall aliases/rules and NAT | Rules/alias applied; automatic NAT retained |
| Kea DHCP subnets/reservations | Supported; Kea is not shown in the current running service list |
| Dnsmasq DHCP ranges | Not available in this provider release |

Do not switch DHCP servers just to fit Terraform. Keep the working service and
use the UI or a focused API/Ansible task for gaps. Do not define the same VLAN in
both Terraform and Ansible. Document any manual interface assignment alongside
the Terraform resources, including the actual assigned `opt` identifiers.

The screenshots confirm Dnsmasq serves LAN with IPv4 pool
192.168.2.6–192.168.2.254 and a separate IPv6 SLAAC entry. Unbound also runs;
Kea and ISC DHCP are not listed. Preserve the existing LAN DHCP and IPv6 settings.

## API credentials

Create a dedicated local automation user in OPNsense under System > Access >
Users. Give it the API permissions needed for the operations being managed.
Generate an API key pair on the user's edit page. This is an API key and secret,
not the account's login password. Save the pair in 1Password; never commit it.
Keep API access limited to trusted local management clients.

The provider reads `OPNSENSE_API_KEY` and `OPNSENSE_API_SECRET`. The
`credentials.env.refs` file uses the supplied `Homelab/OPNsense API` item's
`api_key` and `api_secret` fields. Run authenticated Terraform commands from
the repository root with `bash scripts/opnsense-terraform.sh plan`. No secrets
are stored in the refs file. The wrapper injects them with `op run`.
The local ignored `terraform.tfvars` can override `router_url` to the actual
OPNsense web origin/port. Trust the certificate issuer through `SSL_CERT_FILE`
or system trust; this can be a different CA from Caddy. TLS verification is on.

The router UI/API URL is confirmed as `https://192.168.2.1`. To verify API access
without changing configuration, run from the repository root:

```sh
op run --env-file=terraform/opnsense/credentials.env.refs -- \
  python3 scripts/opnsense-discover.py --ca-file certs/opnsense-root.crt
```

Use the router issuer's exported public certificate for `--ca-file`. If not yet
available, `--insecure` is an explicit local bootstrap alternative to that flag;
it does not change TLS settings on the router or in Terraform. Discovery uses
only GET search endpoints and prints allowlisted fields, never credentials or
raw configuration. HTTP 401 indicates authentication failure; HTTP 403 usually
means the API user's privileges are insufficient for the indicated endpoint.

```sh
cd terraform/opnsense
terraform init
terraform validate
# With API credentials loaded and the correct trusted router URL:
terraform plan -var='create_vlans=true'
```

The VLAN devices were created with these UUIDs:

- IoT (20): `b8bef6ac-4ea4-4c6e-9688-9986cfa57ca6`
- Guest (30): `5611421d-1343-49dd-9e6d-b546244d5a4a`

The provider returns an empty `device` output when it generated the name.
Use the read-only discovery script's `vlans.rows[].vlanif` to find actual names.
After this successful apply, expect no VLAN device changes on subsequent plans.
If a
VLAN already exists, import it by UUID first, for example:

```sh
terraform import -var='create_vlans=true' \
  'opnsense_interfaces_vlan.network["iot"]' EXISTING_VLAN_UUID
```

Keep `create_vlans = true` (the checked-in default): turning it off later
requests deletion, which `prevent_destroy` blocks. An empty plan without any
configured resources is not a connection test. The VLANs have been applied by
the user; assignments and DHCP/firewall configuration are also complete.

After VLAN creation, assign the generated VLAN devices through Interfaces >
Assignments. Enable IoT at 192.168.20.1/24 and Guest at 192.168.30.1/24 with no
upstream gateways. DHCP and isolation rules must be ready before clients migrate.
Preserve the native Home network on `ue1` and on the switch/AP trunk links.

## VLAN DHCP through Ansible

The user has assigned `vlan02` (IoT) and `vlan01` (Guest) with their addresses.
The playbook discovers the actual `opt` identifiers by matching the device and
checks each enabled assignment's address. Desired pools are versioned in
`ansible/vars/opnsense_networks.yml`:

| Network | Pool | Lease | DHCP DNS option |
| --- | --- | --- | --- |
| IoT | 192.168.20.100–192.168.20.199 | 24 hours | 192.168.2.151 (AdGuard) |
| Guest | 192.168.30.100–192.168.30.199 | 24 hours | 192.168.2.151 (AdGuard) |

From the repository root, preview through the existing 1Password references:

```sh
bash scripts/opnsense-dhcp.sh --check \
  -e '{"opnsense_validate_certs": false}'
```

The explicit certificate override is for the current local bootstrap setup.
For verified TLS, export the router issuer certificate and configure Ansible's
URI `ca_path`/system trust, then keep `opnsense_validate_certs: true`.
The preview reads the API and displays intended pool/option changes without
writing. After review, rerun without `--check` to apply.

Only records with the stable `Townhaus IoT/Guest DHCP/DNS` descriptions are
managed. Duplicate or other pools on either new interface block reconciliation.
Existing Home ranges, options and IPv6 SLAAC remain untouched. Explicit Dnsmasq
listener selections are extended with the new interfaces; an empty selection
(all interfaces) is preserved. One reconfigure runs after actual changes.
Changing description strings changes record ownership; review the existing
records instead of creating duplicates. The playbook never deletes DHCP pools.

This does not create firewall rules or change UniFi ports/SSIDs. Rules must allow
DNS from both new VLANs to AdGuard TCP/UDP 53, with other routed LAN access blocked
except intended IoT/Home Assistant traffic. Configure those policies before
connecting clients. API-write tasks hide credentials and response bodies; when
an API write fails, review privileges/validation before retrying. Local mock API
verification covered a write-free preview, initial creation and a repeat apply
with zero additional writes; live OPNsense validation is still required.

References:

- https://github.com/browningluke/terraform-provider-opnsense#current-api-coverage
- https://registry.terraform.io/providers/browningluke/opnsense/0.26.0/docs
- https://docs.opnsense.org/development/how-tos/api.html#creating-keys

## Firewall isolation

`firewall.tf` adds one alias and thirteen rules (14 resources). IoT is confirmed
as `opt1`, Guest as `opt2`. Each interface gets ordered quick rules:

| Sequence | Policy |
| --- | --- |
| 10 | Allow IPv4 DHCP client requests (UDP 68 to 67) |
| 20 | Allow IPv4 DNS to AdGuard 192.168.2.151 on TCP/UDP 53 |
| 30 | Block routed IPv6; these VLANs have no IPv6 addressing/RA configured |
| 40 | Block IPv4 access to all router addresses, including its public WAN IP |
| 50 | Block private/VPN/link-local/non-unicast IPv4 destinations |
| 60 | Allow IPv4 internet from the interface's expected subnet |

A separate stateful LAN rule allows Home Assistant (192.168.2.91) to initiate
connections to IoT. Replies use existing states; IoT cannot initiate connections
to Home Assistant except through a later explicit exception. Existing Home/LAN
rules remain in effect, so this does not make Home Assistant the only trusted
LAN host that can access IoT. Same-VLAN traffic does not cross the router; Guest
Wi-Fi client isolation must also be enabled when that SSID is created.

No rules are floating, and none target WAN. IPv6 on the existing Home LAN stays
as-is. Internet allow resources depend on all managed isolation rules, so they
are not added before the blocks during initial creation. This is IPv4-only
internet access for the new VLANs. Multicast discovery and device-initiated HA
callbacks need separately scoped configuration when required.

Before applying, review the discovery `firewall_rules` inventory and the GUI's
floating/group/legacy rules for earlier broad pass rules. The API inventory is
not guaranteed to include all legacy rules. Review outbound NAT mode too:
automatic mode should cover new internal subnets, while manual mode needs
explicit NAT rules. This configuration does not change NAT mode or add NAT.

```sh
op run --env-file=terraform/opnsense/credentials.env.refs -- \
  python3 scripts/opnsense-discover.py --insecure
bash scripts/opnsense-terraform.sh plan -var='allow_insecure=true'
```

With VLANs already in state, expect **14 to add, 0 to change, 0 to destroy**.
Inspect the rule scope/order before applying. Test DHCP, DNS, internet, denial
of router/admin and LAN access, and HA-to-IoT connectivity with a test client
before moving existing devices. Existing state entries can outlive changed
rules; use fresh connections for verification. The user has now applied these firewall rules and confirmed a no-change plan.
Automatic source NAT includes both IoT and Guest, as verified in the UI.
UniFi VLANs and both new SSIDs have also been applied. The user verified client
connectivity and isolation; both Terraform roots report no changes.
