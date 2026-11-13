# Camera network

The Cameras network is active on VLAN 40, 192.168.40.0/24. OPNsense routes it
on `ue1` through `vlan03` / `opt3`, with gateway 192.168.40.1 and Dnsmasq pool
192.168.40.100–192.168.40.199. The VLAN was configured in OPNsense and UniFi;
the router and inter-switch uplinks carry its tagged traffic.

USW Flex 2.5G 5 1st Floor port 1 (`74:f9:2c:98:1e:a3`) connects an unmanaged
PoE switch. Terraform manages that port with Cameras as the native network and
all tagged VLANs blocked. No camera Wi-Fi SSID is configured.

| Device | MAC | Reserved IP |
| --- | --- | --- |
| ESP32Alarm | 44:1b:f6:d3:7d:97 (Ethernet) | 192.168.40.10 |
| Garage Sensor ESP32 | 3c:0f:02:d7:e2:ff (Ethernet) | 192.168.40.11 |
| Nico / Oficina camera | a0:60:32:05:32:0a | 192.168.40.20 |
| Backyard camera | a0:60:32:0a:0a:b2 | 192.168.40.21 |
| Reolink doorbell | ec:71:db:c3:84:21 | 192.168.40.22 |

The three cameras' existing OPNsense reservations were imported and updated;
do not create duplicate reservations. Frigate runs on the Beelink on the LAN.
Its six 1Password RTSP URL fields point to the addresses above; the `oficina`
fields refer to Nico. Deploy its configuration with:

```sh
ansible-playbook ansible/playbooks/frigate.yml --limit beelink.local --ask-become-pass
```

Home Assistant connects to ESP32Alarm at 192.168.40.10; the garage sensor uses
192.168.40.11. The current OPNsense LAN rules allow LAN-initiated connections.
Camera-interface rules allow DHCP and block other initiated IPv4 and IPv6
traffic, including internet, DNS and NTP. Stateful replies to permitted
LAN-initiated connections are allowed. The firewall does not isolate devices
from each other within the same VLAN. Camera streams and both ESP devices were
reported working; device-originated isolation and OTA tests remain to be done.

## Terraform settings and state

Camera resources are opt-in in the checked-in Terraform configuration. The
following **ignored local** files are necessary on this installation to reflect
what has already been applied. Keep Terraform state and backups protected; do
not recreate resources or import them again without reviewing state and a plan.

`terraform/opnsense/camera.auto.tfvars`:

```hcl
create_camera_vlan                  = true
camera_interface                    = "opt3"
create_camera_reservations          = true
manage_existing_camera_reservations = true
move_camera_reservations            = true
```

`terraform/unifi/camera.auto.tfvars`:

```hcl
create_camera_network = true
manage_camera_port    = true
```

Removing these values changes the desired configuration and can plan resource
removals or reversal of camera reservation addresses. Some resources have
`prevent_destroy`, but the firewall rules and ESP reservations do not. Review
every plan before applying it. After restoring a controller backup, verify the
interface IDs, reservation IDs, device MAC and Terraform state before applying.

## DHCP and diagnostics

The camera DHCP pool is managed with the existing Ansible workflow, using a
camera-only variable file. Preview or apply with:

```sh
bash scripts/opnsense-dhcp.sh --check \
  -e @ansible/vars/opnsense_camera_dhcp.yml \
  -e '{"opnsense_validate_certs": false}'
```

Omit `--check` only after reviewing the preview. This uses the local bootstrap
TLS exception; prefer a trusted router certificate when available. No explicit
DNS option is configured for this pool. Existing IoT and Guest pools remain
managed through the default variable file.

If DHCP requests appear on `vlan03` but Dnsmasq reports that it has no address,
check the **live** interface address, not just the saved assignment. During the
initial cutover, saving and applying Interfaces > Cameras activated
192.168.40.1/24 and DHCP began working. Preserve a backup and a rollback path
before changing the switch port; moving it affects all five devices at once.
If rolling back, coordinate the old port network, OPNsense reservations,
1Password stream URLs, Frigate deployment, and Home Assistant addresses.
