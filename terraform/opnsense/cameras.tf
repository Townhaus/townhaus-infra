# Opt in only during the staged camera migration; review docs/camera-network.md.
variable "create_camera_vlan" {
  type        = bool
  default     = false
  description = "Create the proposed camera VLAN on ue1. Keep enabled after creation."
}

variable "camera_interface" {
  type        = string
  default     = ""
  description = "Actual OPNsense opt identifier after the camera VLAN is assigned; leave empty until verified."

  validation {
    condition     = var.camera_interface == "" || can(regex("^opt[0-9]+$", var.camera_interface))
    error_message = "camera_interface must be empty or an OPNsense opt identifier."
  }
}

variable "create_camera_reservations" {
  type        = bool
  default     = false
  description = "Create Ethernet ESPHome reservations after checking for conflicts."
}

variable "manage_existing_camera_reservations" {
  type        = bool
  default     = false
  description = "Import and manage the three existing camera reservations; keep enabled after import."
}

variable "move_camera_reservations" {
  type        = bool
  default     = false
  description = "Change the imported camera reservations to their camera VLAN addresses during cutover."
}

resource "opnsense_interfaces_vlan" "cameras" {
  count       = var.create_camera_vlan ? 1 : 0
  description = "Cameras"
  parent      = "ue1"
  tag         = 40
  priority    = 0

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  camera_reservations = {
    alarm = {
      hostname = "esphome-web-d37d94"
      ip       = "192.168.40.10"
      mac      = "44:1b:f6:d3:7d:97"
    }
    garage = {
      hostname = "garage-sensor-esp32"
      ip       = "192.168.40.11"
      mac      = "3c:0f:02:d7:e2:ff"
    }
  }
  existing_camera_reservations = {
    nico = {
      uuid     = "6b2f3c36-904e-45a7-8557-f760f8d90afe"
      hostname = "nico-camera"
      lan_ip   = "192.168.2.138"
      vlan_ip  = "192.168.40.20"
      mac      = "a0:60:32:05:32:0a"
    }
    backyard = {
      uuid     = "b642d344-778d-4a16-a8a7-7870ea6c5d63"
      hostname = "backyard-camera"
      lan_ip   = "192.168.2.135"
      vlan_ip  = "192.168.40.21"
      mac      = "a0:60:32:0a:0a:b2"
    }
    doorbell = {
      uuid     = "a8a3a15a-9c21-4782-ab7b-2a8e07825d72"
      hostname = "reolink-doorbell"
      lan_ip   = "192.168.2.137"
      vlan_ip  = "192.168.40.22"
      mac      = "ec:71:db:c3:84:21"
    }
  }
}

import {
  for_each = var.manage_existing_camera_reservations ? local.existing_camera_reservations : {}
  to       = opnsense_dnsmasq_host.existing_camera[each.key]
  id       = each.value.uuid
}

resource "opnsense_dnsmasq_host" "existing_camera" {
  for_each = var.manage_existing_camera_reservations ? local.existing_camera_reservations : {}

  hostname           = each.value.hostname
  ip_addresses       = [var.move_camera_reservations ? each.value.vlan_ip : each.value.lan_ip]
  hardware_addresses = [each.value.mac]
  description        = "Migrated from UniFi reservation"
  is_ignored         = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "opnsense_dnsmasq_host" "camera_device" {
  for_each = var.create_camera_reservations ? local.camera_reservations : {}

  hostname           = each.value.hostname
  ip_addresses       = [each.value.ip]
  hardware_addresses = [each.value.mac]
  description        = "Townhaus camera VLAN ${each.key} DHCP reservation"
  is_ignored         = false
}

# DHCP is allowed first. All other camera-initiated traffic is blocked,
# including Internet and access to the firewall. LAN-initiated connections
# and their stateful replies are controlled by the existing LAN policy.
resource "opnsense_firewall_filter" "camera_dhcp" {
  count       = var.camera_interface != "" ? 1 : 0
  enabled     = true
  description = "Townhaus cameras DHCP"
  sequence    = 10
  interface   = { interface = [var.camera_interface] }
  filter = {
    action      = "pass", direction = "in", quick = true
    ip_protocol = "inet", protocol = "UDP"
    source      = { net = "any", port = "68" }
    destination = { net = "any", port = "67" }
  }
}

resource "opnsense_firewall_filter" "camera_block" {
  count       = var.camera_interface != "" ? 1 : 0
  enabled     = true
  description = "Townhaus cameras block outbound"
  sequence    = 20
  interface   = { interface = [var.camera_interface] }
  filter = {
    action      = "block", direction = "in", quick = true
    ip_protocol = "inet46", protocol = "any"
    source      = { net = "any" }
    destination = { net = "any" }
  }
}
