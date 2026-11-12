locals {
  # Confirmed by the DHCP live preview, not inferred from creation order.
  vlan_interfaces = {
    iot   = { interface = "opt1", subnet = "192.168.20.0/24" }
    guest = { interface = "opt2", subnet = "192.168.30.0/24" }
  }

  restricted_vlan_rules = merge([
    for name, network in local.vlan_interfaces : {
      "${name}_dhcp" = {
        interface = network.interface
        sequence  = 10
        filter = {
          action      = "pass", direction = "in", quick = true
          ip_protocol = "inet", protocol = "UDP"
          source      = { net = "any", port = "68" }
          destination = { net = "any", port = "67" }
        }
      }
      "${name}_dns" = {
        interface = network.interface
        sequence  = 20
        filter = {
          action      = "pass", direction = "in", quick = true
          ip_protocol = "inet", protocol = "TCP/UDP"
          source      = { net = network.subnet }
          destination = { net = "192.168.2.151", port = "53" }
        }
      }
      "${name}_block_ipv6" = {
        interface = network.interface
        sequence  = 30
        filter = {
          action      = "block", direction = "in", quick = true
          ip_protocol = "inet6", protocol = "any"
          source      = { net = "any" }
          destination = { net = "any" }
        }
      }
      "${name}_block_router" = {
        interface = network.interface
        sequence  = 40
        filter = {
          action      = "block", direction = "in", quick = true
          ip_protocol = "inet", protocol = "any"
          source      = { net = "any" }
          destination = { net = "(self)" }
        }
      }
      "${name}_block_internal" = {
        interface = network.interface
        sequence  = 50
        filter = {
          action      = "block", direction = "in", quick = true
          ip_protocol = "inet", protocol = "any"
          source      = { net = "any" }
          destination = { net = opnsense_firewall_alias.internal_ipv4.name }
        }
      }
    }
  ]...)
}

resource "opnsense_firewall_alias" "internal_ipv4" {
  name        = "Townhaus_Internal_IPv4"
  type        = "network"
  description = "Private, VPN, link-local and non-unicast destinations blocked from IoT/Guest"
  content = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "224.0.0.0/4",
    "240.0.0.0/4",
  ]
}

resource "opnsense_firewall_filter" "vlan_policy" {
  for_each = local.restricted_vlan_rules

  enabled     = true
  description = "Townhaus ${each.key}"
  sequence    = each.value.sequence
  interface   = { interface = [each.value.interface] }
  filter      = each.value.filter
}

resource "opnsense_firewall_filter" "vlan_internet" {
  for_each = local.vlan_interfaces

  enabled     = true
  description = "Townhaus ${each.key} IPv4 internet"
  sequence    = 60
  interface   = { interface = [each.value.interface] }
  filter = {
    action      = "pass", direction = "in", quick = true
    ip_protocol = "inet", protocol = "any"
    source      = { net = each.value.subnet }
    destination = { net = "any" }
  }

  # Establish isolation before adding the broad internet allow rules.
  depends_on = [opnsense_firewall_filter.vlan_policy]
}

resource "opnsense_firewall_filter" "home_assistant_to_iot" {
  enabled     = true
  description = "Townhaus Home Assistant initiates connections to IoT"
  sequence    = 70
  interface   = { interface = ["lan"] }
  filter = {
    action      = "pass", direction = "in", quick = true
    ip_protocol = "inet", protocol = "any"
    source      = { net = "192.168.2.91" }
    destination = { net = local.vlan_interfaces.iot.subnet }
  }
}
