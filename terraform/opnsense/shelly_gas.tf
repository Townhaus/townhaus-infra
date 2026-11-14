# Allow only the gas sensor's CoIoT updates before the IoT private-network block.
resource "opnsense_firewall_filter" "shelly_gas_coiot" {
  enabled     = true
  description = "Townhaus Shelly Gas CoIoT to Home Assistant"
  sequence    = 27
  interface   = { interface = [local.vlan_interfaces.iot.interface] }
  filter = {
    action      = "pass", direction = "in", quick = true
    ip_protocol = "inet", protocol = "UDP"
    source      = { net = local.iot_dhcp_reservations.shelly_gas.ip_address }
    destination = { net = "192.168.2.91", port = "5683" }
  }
}
