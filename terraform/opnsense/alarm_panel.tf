# Panel-specific exceptions before the private-network block at sequence 50.
resource "opnsense_firewall_filter" "alarm_panel_mqtt" {
  enabled     = true
  description = "Townhaus alarm panel to Home Assistant MQTT"
  sequence    = 25
  interface   = { interface = [local.vlan_interfaces.iot.interface] }
  filter = {
    action      = "pass", direction = "in", quick = true
    ip_protocol = "inet", protocol = "TCP"
    source      = { net = local.iot_dhcp_reservations.alarm_panel.ip_address }
    destination = { net = "192.168.2.91", port = "1883" }
  }
}

resource "opnsense_firewall_filter" "alarm_panel_ota_callback" {
  enabled     = true
  description = "Townhaus alarm panel OTA callback to deployment host"
  sequence    = 26
  interface   = { interface = [local.vlan_interfaces.iot.interface] }
  filter = {
    action      = "pass", direction = "in", quick = true
    ip_protocol = "inet", protocol = "TCP"
    source      = { net = local.iot_dhcp_reservations.alarm_panel.ip_address }
    destination = { net = "192.168.2.169", port = "3232" }
  }
}
