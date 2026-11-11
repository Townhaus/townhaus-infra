terraform {
  required_version = ">= 1.11.0, < 2.0.0"

  required_providers {
    opnsense = {
      source  = "browningluke/opnsense"
      version = "0.26.0"
    }
  }
}

# Credentials come from OPNSENSE_API_KEY and OPNSENSE_API_SECRET.
provider "opnsense" {
  uri            = var.router_url
  allow_insecure = var.allow_insecure
}

variable "router_url" {
  description = "OPNsense HTTPS origin; change this if the UI uses a different port or trusted hostname."
  type        = string
  default     = "https://192.168.2.1"
}

variable "allow_insecure" {
  description = "Explicit opt-in to skip certificate verification during local bootstrap. Prefer a trusted certificate."
  type        = bool
  default     = false
}

variable "create_vlans" {
  description = "Keep enabled: VLANs 20 and 30 have been created and are managed in state."
  type        = bool
  default     = true
}

locals {
  vlans = {
    iot   = { description = "IoT", tag = 20 }
    guest = { description = "Guest", tag = 30 }
  }
}

resource "opnsense_interfaces_vlan" "network" {
  for_each = var.create_vlans ? local.vlans : {}

  description = each.value.description
  parent      = "ue1"
  tag         = each.value.tag
  priority    = 0

  lifecycle {
    prevent_destroy = true
  }
}

output "vlan_devices" {
  description = "Devices to assign in OPNsense after VLAN creation. This does not assign interfaces or addresses."
  value = {
    for name, vlan in opnsense_interfaces_vlan.network : name => {
      id     = vlan.id
      device = vlan.device
      tag    = vlan.tag
    }
  }
}
