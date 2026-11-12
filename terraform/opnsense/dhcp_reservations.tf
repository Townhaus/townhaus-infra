locals {
  # Keep existing IoT leases; assign addresses outside the pool for future migrations.
  iot_dhcp_reservations = {
    black_light = {
      hostname    = "black-light"
      ip_address  = "192.168.20.179"
      mac_address = "2c:f4:32:c2:ab:8a"
      description = "Townhaus black_light ESPHome DHCP reservation"
    }
    alarm_panel = {
      hostname    = "alarm-panel"
      ip_address  = "192.168.20.50"
      mac_address = "d8:13:2a:73:c6:e8"
      description = "Townhaus alarm panel TARS DHCP reservation"
    }
    presence = {
      hostname    = "esp32-presence-node"
      ip_address  = "192.168.20.137"
      mac_address = "44:1b:f6:fe:66:ac"
      description = "Townhaus presence ESPHome DHCP reservation"
    }
    lounge = {
      hostname    = "lounge"
      ip_address  = "192.168.20.51"
      mac_address = "98:f4:ab:b2:5c:c2"
      description = "Townhaus lounge ESPHome DHCP reservation"
    }
    pvr_lightbox = {
      hostname    = "pvr-lightbox"
      ip_address  = "192.168.20.52"
      mac_address = "cc:50:e3:70:59:6e"
      description = "Townhaus PVR lightbox ESPHome DHCP reservation"
    }
    gym = {
      hostname    = "gym"
      ip_address  = "192.168.20.53"
      mac_address = "2c:f4:32:49:db:db"
      description = "Townhaus gym ESPHome DHCP reservation"
    }
    fosi_zd3_ir = {
      hostname    = "fosi-zd3-ir"
      ip_address  = "192.168.20.54"
      mac_address = "e8:6b:ea:23:7e:14"
      description = "Townhaus Fosi IR ESPHome DHCP reservation"
    }
    living_room = {
      hostname    = "living-room"
      ip_address  = "192.168.20.55"
      mac_address = "dc:4f:22:3d:71:09"
      description = "Townhaus living room ESPHome DHCP reservation"
    }
    oficina = {
      hostname    = "oficina"
      ip_address  = "192.168.20.56"
      mac_address = "44:1b:f6:d4:52:c0"
      description = "Townhaus oficina ESPHome DHCP reservation"
    }
  }
}

# DHCP host reservations are supported by this provider; Dnsmasq pools/options
# remain managed by Ansible. Do not define these same hosts in the playbook.
resource "opnsense_dnsmasq_host" "iot" {
  for_each = local.iot_dhcp_reservations

  hostname           = each.value.hostname
  ip_addresses       = [each.value.ip_address]
  hardware_addresses = [each.value.mac_address]
  description        = each.value.description
  is_ignored         = false
}
